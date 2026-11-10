"""Rate limiting auth-эндпоинтов (STAGE_1_PLAN §9.3, SECURITY §2).

Ключи — HMAC-SHA256 с CP_RATELIMIT_HMAC_KEY (email/IP в открытом виде не хранятся).
Enforcement — 429 + Retry-After; длительные asyncio.sleep НЕ используются.
Redis недоступен -> ExternalServiceError 503 (fail-closed: лимиты не отключаются
молча; SECURITY §2, решение владельца).
"""

import hashlib
import hmac
from typing import Protocol

import redis.asyncio as redis_asyncio

from app.core.config import Settings
from app.core.errors import ExternalServiceError, RateLimitError

__all__ = ["NoOpRateLimiter", "RateLimiter", "RedisRateLimiter"]


class RateLimiter(Protocol):
    """Контракт лимитера для auth-эндпоинтов (429 -> RateLimitError)."""

    async def check_auth_ip(self, ip: str) -> None: ...

    async def check_login_ip(self, ip: str) -> None: ...

    async def check_login_failures(self, email: str) -> None: ...

    async def register_login_failure(self, email: str) -> None: ...

    async def clear_login_failures(self, email: str) -> None: ...


class NoOpRateLimiter:
    """Лимиты отключены (тестовые сценарии ротации/CSRF без Redis)."""

    async def check_auth_ip(self, ip: str) -> None:
        return None

    async def check_login_ip(self, ip: str) -> None:
        return None

    async def check_login_failures(self, email: str) -> None:
        return None

    async def register_login_failure(self, email: str) -> None:
        return None

    async def clear_login_failures(self, email: str) -> None:
        return None


class RedisRateLimiter:
    """Реализация на Redis: fixed-window per-IP/per-account, ключи через HMAC."""

    def __init__(self, client: redis_asyncio.Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._hmac_key = settings.ratelimit_hmac_key.encode("utf-8")

    def _hmac(self, value: str) -> str:
        return hmac.new(self._hmac_key, value.lower().encode("utf-8"), hashlib.sha256).hexdigest()

    async def _incr(self, key: str, window_seconds: int, limit: int) -> None:
        """Атомарный INCR + EXPIRE NX + TTL через MULTI/EXEC (pipeline).

        Транзакция Redis гарантирует: конкурентные инкременты не теряются,
        TTL устанавливается ровно один раз (первым инкрементом).
        """
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_seconds, nx=True)
                pipe.ttl(key)
                count, _, ttl = await pipe.execute()
        except Exception as exc:  # redis.RedisError и ошибки соединения
            raise ExternalServiceError("аутентификация временно недоступна") from exc
        if int(count) > limit:
            retry_after = max(int(ttl), 1)
            raise RateLimitError(details={"retryAfterSeconds": retry_after})

    async def check_auth_ip(self, ip: str) -> None:
        await self._incr(
            f"cp:rl:auth:ip:{self._hmac(ip)}",
            60,
            self._settings.rate_limit_auth_per_minute_ip,
        )

    async def check_login_ip(self, ip: str) -> None:
        await self._incr(
            f"cp:rl:login:ip:{self._hmac(ip)}",
            60,
            self._settings.rate_limit_login_per_minute_ip,
        )

    async def check_login_failures(self, email: str) -> None:
        await self._incr(
            f"cp:rl:acc:{self._hmac(email)}",
            self._settings.rate_limit_login_failures_window_seconds,
            self._settings.rate_limit_login_failures_per_account,
        )

    async def register_login_failure(self, email: str) -> None:
        """Неудачный логин: инкремент счётчика; сверх порога — 429."""
        await self.check_login_failures(email)

    async def clear_login_failures(self, email: str) -> None:
        try:
            await self._client.delete(f"cp:rl:acc:{self._hmac(email)}")
        except Exception as exc:
            raise ExternalServiceError("аутентификация временно недоступна") from exc
