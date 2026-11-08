"""База данных: async-engine, Base с naming conventions, session-factory, get_session.

Naming conventions — DATABASE §1 (idx_/uq_/fk_/pk_, snake_case, множественное число).
Граница транзакции HTTP-запроса: get_session — commit при успехе, rollback при ошибке.
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import Request

__all__ = ["Base", "create_db_engine", "create_session_factory", "get_session"]

_NAMING_CONVENTION = {
    "ix": "idx_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый класс моделей с конвенциями именования DATABASE §1."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


def create_db_engine(database_url: str) -> AsyncEngine:
    """Создать async-engine (pool_pre_ping — быстрый детект мёртвых соединений)."""
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий для engine (одна на приложение, lifespan)."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия на запрос; commit при успехе, rollback при ошибке."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
