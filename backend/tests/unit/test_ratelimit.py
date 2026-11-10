"""S1.5 unit: RedisRateLimiter — окна, HMAC-ключи, конкурентность, fail-closed."""

import asyncio
from typing import Any

import pytest
from fakeredis import aioredis as fakeredis_aio

from app.core.config import Settings
from app.core.errors import ExternalServiceError, RateLimitError
from app.core.ratelimit import RedisRateLimiter

HMAC_KEY = "unit-test-hmac-key-0123456789abcdef0123456789"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://x@localhost/x",
        redis_url="redis://localhost:6379/0",
        jwt_secret="x" * 48,
        ratelimit_hmac_key=HMAC_KEY,
        **overrides,
    )


@pytest.fixture
def fake_client() -> Any:
    return fakeredis_aio.FakeRedis()


async def test_auth_ip_limit_30_per_minute(fake_client: Any) -> None:
    limiter = RedisRateLimiter(fake_client, _settings())
    for _ in range(30):
        await limiter.check_auth_ip("10.0.0.1")
    with pytest.raises(RateLimitError) as excinfo:
        await limiter.check_auth_ip("10.0.0.1")
    assert excinfo.value.details["retryAfterSeconds"] >= 1


async def test_login_ip_limit_10_per_minute(fake_client: Any) -> None:
    limiter = RedisRateLimiter(fake_client, _settings())
    for _ in range(10):
        await limiter.check_login_ip("10.0.0.2")
    with pytest.raises(RateLimitError):
        await limiter.check_login_ip("10.0.0.2")


async def test_per_account_failures_and_reset(fake_client: Any) -> None:
    limiter = RedisRateLimiter(fake_client, _settings())
    for _ in range(10):
        await limiter.register_login_failure("User@Example.com")
    with pytest.raises(RateLimitError):
        await limiter.check_login_failures("user@example.com")  # case-insensitive

    await limiter.clear_login_failures("USER@example.COM")
    await limiter.check_login_failures("user@example.com")  # снова разрешено


async def test_keys_contain_no_raw_email_or_ip(fake_client: Any) -> None:
    limiter = RedisRateLimiter(fake_client, _settings())
    await limiter.check_auth_ip("10.1.2.3")
    await limiter.register_login_failure("secret@example.com")
    keys = [k.decode() if isinstance(k, bytes) else k for k in await fake_client.keys("*")]
    dumped = " ".join(keys)
    assert "10.1.2.3" not in dumped
    assert "secret@example.com" not in dumped
    assert all(k.startswith("cp:rl:") for k in keys)


async def test_concurrent_incr_is_atomic(fake_client: Any) -> None:
    """20 конкурентных INCR: ни один не потерян, лимит срабатывает ровно на 31-м."""
    limiter = RedisRateLimiter(fake_client, _settings(rate_limit_auth_per_minute_ip=30))
    results = await asyncio.gather(
        *[limiter.check_auth_ip("10.9.9.9") for _ in range(30)],
        return_exceptions=True,
    )
    assert all(r is None for r in results)
    results_31 = await asyncio.gather(
        *[limiter.check_auth_ip("10.9.9.9") for _ in range(5)],
        return_exceptions=True,
    )
    assert all(isinstance(r, RateLimitError) for r in results_31)
    # счётчик не превысил 35 (каждый INCR ровно один раз)
    key = next(iter(await fake_client.keys("cp:rl:auth:ip:*")))
    assert await fake_client.get(key) == b"35"


async def test_ttl_is_set_on_first_increment(fake_client: Any) -> None:
    limiter = RedisRateLimiter(fake_client, _settings())
    await limiter.check_auth_ip("10.3.3.3")
    key = next(iter(await fake_client.keys("cp:rl:auth:ip:*")))
    ttl = await fake_client.ttl(key)
    assert 1 <= ttl <= 60


async def test_redis_outage_fail_closed() -> None:
    """Недоступный Redis (закрытый порт) -> ExternalServiceError (503), НЕ молчим."""
    import redis.asyncio as redis_asyncio

    dead = redis_asyncio.from_url("redis://127.0.0.1:1/0")
    try:
        limiter = RedisRateLimiter(dead, _settings())
        with pytest.raises(ExternalServiceError):
            await limiter.check_auth_ip("10.0.0.1")
        with pytest.raises(ExternalServiceError):
            await limiter.clear_login_failures("a@b.com")
    finally:
        await dead.aclose()
