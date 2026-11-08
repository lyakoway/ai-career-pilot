"""Readiness-проверка зависимостей: SELECT 1 к PostgreSQL + PING к Redis.

Зависимости передаются через минимальные Protocol-ы — приложение подключает
реальные engine/redis (core.db, redis.asyncio), тесты — фейки.
"""

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import structlog
from sqlalchemy import text

__all__ = ["ReadinessReport", "check_readiness"]

_logger = structlog.get_logger("readiness")


class RedisGateway(Protocol):
    """Минимальный контракт redis-клиента для readiness."""

    async def ping(self) -> Any: ...


class DatabaseGateway(Protocol):
    """Минимальный контракт engine для readiness (connect() -> async context)."""

    def connect(self) -> AbstractAsyncContextManager[Any]: ...


class ReadinessReport:
    """Итог проверки: ok только если все зависимости здоровы."""

    def __init__(self, checks: dict[str, bool]) -> None:
        self.checks = checks
        self.ok = all(checks.values())


async def check_readiness(engine: DatabaseGateway, redis: RedisGateway) -> ReadinessReport:
    """Проверить PostgreSQL (SELECT 1) и Redis (PING); ошибки — в WARNING-лог."""
    checks: dict[str, bool] = {}

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        _logger.warning("readiness_check_failed", dependency="database", error=str(exc))
        checks["database"] = False

    try:
        await redis.ping()
        checks["redis"] = True
    except Exception as exc:
        _logger.warning("readiness_check_failed", dependency="redis", error=str(exc))
        checks["redis"] = False

    return ReadinessReport(checks)
