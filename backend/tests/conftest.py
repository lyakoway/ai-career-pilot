"""Общие фикстуры тестов backend.

Тестовое окружение устанавливается ДО импорта приложения:
module-level setdefault, чтобы create_app() -> get_settings() прошёл fail-fast.

Интеграционные PG-тесты: источник базы — env CP_TEST_DATABASE_URL (админ)
или testcontainers (если доступен Docker); иначе — skip с пояснением.
SQLite не используется нигде (STAGE_1_PLAN §10).
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_db_engine

_TEST_ENV = {
    "CP_APP_ENV": "local",
    "CP_DATABASE_URL": "postgresql+asyncpg://cp_app:cp_app@localhost:5432/careerpilot_test",
    "CP_REDIS_URL": "redis://localhost:6379/0",
    "CP_JWT_SECRET": "unit-test-jwt-secret-0123456789abcdef0123456789",
    "CP_RATELIMIT_HMAC_KEY": "unit-test-hmac-key-0123456789abcdef0123456789",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

from app.main import create_app  # noqa: E402
from tests.fakes import FakeEngine, FakeRedis  # noqa: E402

BOOTSTRAP_SQL = (
    Path(__file__).resolve().parent.parent / "docker" / "db" / "bootstrap.sql"
).read_text()


@pytest.fixture
def client() -> TestClient:
    """Клиент против свежесобранного приложения со здоровыми фейками зависимостей."""
    return TestClient(create_app(db_engine=FakeEngine(), redis_client=FakeRedis()))


# ---------- PostgreSQL (интеграционные тесты S1.2+) ----------


def _to_asyncpg_url(url: str) -> str:
    if "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _derive_role_url(admin_url: str, role: str) -> str:
    """URL админа -> URL роли (cp_app:cp_app / cp_migrations:cp_migrations)."""
    return re.sub(r"://[^@/]+@", f"://{role}:{role}@", admin_url, count=1)


def _split_statements(script: str) -> list[str]:
    """Разбить SQL-скрипт на стейтменты (asyncpg не принимает multi-statement).

    Уважает dollar-quoted блоки ($$ ... $$) и построчные комментарии `--`
    (внутри комментария ';' не разделяет; сами комментарии выбрасываются).
    """
    statements: list[str] = []
    current: list[str] = []
    in_dollar_block = False
    i = 0
    n = len(script)
    while i < n:
        if not in_dollar_block and script.startswith("--", i):
            end_of_line = script.find("\n", i)
            i = n if end_of_line == -1 else end_of_line  # комментарий выбрасывается
            continue
        if script.startswith("$$", i):
            in_dollar_block = not in_dollar_block
            current.append("$$")
            i += 2
            continue
        char = script[i]
        if char == ";" and not in_dollar_block:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


async def _exec_script(url: str, script: str) -> None:
    engine = create_db_engine(url)
    try:
        async with engine.begin() as conn:
            for statement in _split_statements(script):
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _reset_to_base(admin_url: str) -> None:
    """Привести базу к состоянию 'до миграций' (устойчиво к любым остаткам)."""
    engine = create_db_engine(admin_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DROP TABLE IF EXISTS audit_events, audit_subjects, "
                    "refresh_tokens, users, alembic_version CASCADE"
                )
            )
            await conn.execute(text("DROP EXTENSION IF EXISTS citext"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def pg() -> Iterator[dict[str, str]]:
    """URL'ы тестовой PG (admin/app/migrations) + bootstrap ролей; skip если PG нет."""
    admin = os.environ.get("CP_TEST_DATABASE_URL", "")
    owned_container = None
    if not admin:
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            pytest.skip("PostgreSQL недоступен: задайте CP_TEST_DATABASE_URL или установите Docker")
        try:
            owned_container = PostgresContainer("postgres:16")
            owned_container.start()
            admin = owned_container.get_connection_url()
        except Exception as exc:
            pytest.skip(f"testcontainers не смог поднять PostgreSQL: {exc}")

    admin_async = _to_asyncpg_url(admin)
    urls = {
        "admin": admin_async,
        "app": _to_asyncpg_url(
            os.environ.get("CP_TEST_APP_DATABASE_URL") or _derive_role_url(admin_async, "cp_app")
        ),
        "migrations": _to_asyncpg_url(
            os.environ.get("CP_TEST_MIGRATIONS_DATABASE_URL")
            or _derive_role_url(admin_async, "cp_migrations")
        ),
    }
    asyncio.run(_reset_to_base(urls["admin"]))
    asyncio.run(_exec_script(urls["admin"], BOOTSTRAP_SQL))
    # Приводим схему к head здесь: остальные PG-тесты (в т.ч. изолированные
    # запуски одного файла) не зависят от порядка и от cycle-теста миграций.
    os.environ["CP_MIGRATIONS_DATABASE_URL"] = urls["migrations"]
    from alembic.config import Config as AlembicConfig

    from alembic import command

    command.upgrade(AlembicConfig("alembic.ini"), "head")
    yield urls
    if owned_container is not None:
        owned_container.stop()


async def _fetch_all(
    url: str, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    engine = create_db_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()


def fetch_all(url: str, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Синхронная обёртка для запросов из sync-тестов (миграции)."""
    return asyncio.run(_fetch_all(url, sql, params))


async def _write(url: str, sql: str, params: dict[str, Any] | None = None) -> None:
    engine = create_db_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


def run_write(url: str, sql: str, params: dict[str, Any] | None = None) -> None:
    """Синхронная обёртка для записей из sync-тестов (в транзакции)."""
    asyncio.run(_write(url, sql, params))


_TABLES = ("audit_events", "audit_subjects", "refresh_tokens", "users")


async def _truncate(admin_url: str) -> None:
    engine = create_db_engine(admin_url)
    try:
        async with engine.begin() as conn:
            for table in _TABLES:
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(pg: dict[str, str]) -> AsyncIterator[AsyncSession]:
    """Сессия под ролью cp_app — тестирует реальные GRANT'ы (STAGE_1_PLAN §10).

    Перед выдачей таблицы очищаются (тест-данные создаются самим тестом).
    """
    await _truncate(pg["admin"])
    factory = async_sessionmaker(create_db_engine(pg["app"]), expire_on_commit=False)
    async with factory() as session:
        yield session
