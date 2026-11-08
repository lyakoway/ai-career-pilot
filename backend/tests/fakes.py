"""Фейки зависимостей для тестов /ready (Protocol-ы из core.readiness)."""

from typing import Any


class FakeConnection:
    """Соответствует AsyncConnection в объёме readiness (execute)."""

    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def execute(self, statement: Any) -> Any:
        if not self._healthy:
            raise RuntimeError("database is down")
        return None


class _ConnectContext:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def __aenter__(self) -> FakeConnection:
        if not self._healthy:
            raise RuntimeError("database connect failed")
        return FakeConnection(self._healthy)

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    """Соответствует Protocol DatabaseGateway: connect() -> async context."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def connect(self) -> _ConnectContext:
        return _ConnectContext(self.healthy)


class FakeRedis:
    """Соответствует Protocol RedisGateway: ping()."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def ping(self) -> Any:
        if not self.healthy:
            raise RuntimeError("redis is down")
        return True
