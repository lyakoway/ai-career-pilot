"""Контракт rate limiting auth-эндпоинтов (STAGE_1_PLAN §9.3).

S1.4 — интерфейс + NoOp-реализация (зависимость уже вшита в auth-роутер);
S1.5 подменит NoOp на Redis-реализацию (fixed-window per-IP/per-account,
fail-closed 503 при недоступности Redis) без изменений в роутере.
"""

from typing import Protocol

__all__ = ["NoOpRateLimiter", "RateLimiter"]


class RateLimiter(Protocol):
    """Проверить лимит; при превышении — поднять RateLimitError (429)."""

    async def check(self, *, key: str, limit: int, window_seconds: int) -> None: ...


class NoOpRateLimiter:
    """S1.4: ограничение отключено (реальная реализация — S1.5)."""

    async def check(self, *, key: str, limit: int, window_seconds: int) -> None:
        return None
