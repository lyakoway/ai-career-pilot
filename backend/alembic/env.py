"""Async Alembic env: metadata из приложения, URL из окружения.

Порядок источника URL: CP_MIGRATIONS_DATABASE_URL (роль cp_migrations — DDL),
затем CP_DATABASE_URL (STAGE_1_PLAN §8.2: миграции — под отдельной ролью).
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.db import Base
from app.modules.career_profile import models as _cp_models  # noqa: F401 — metadata
from app.modules.identity import models  # noqa: F401 — регистрация моделей в metadata
from app.modules.resume_intelligence import (  # noqa: F401 — metadata
    models as _ri_models,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    migrations_url = os.environ.get("CP_MIGRATIONS_DATABASE_URL", "")
    return migrations_url or get_settings().database_url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
