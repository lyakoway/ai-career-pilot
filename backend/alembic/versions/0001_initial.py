"""identity: users, refresh_tokens, audit_subjects, audit_events + роли-гранты

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-08

Схема — DATABASE.md §3.1, §3.15 (STAGE_1_PLAN §8.1).
Роли cp_app/cp_migrations создаются инфраструктурным bootstrap'ом (docker/db/bootstrap.sql),
миграция выдаёт GRANT: cp_app — DML на доменные таблицы, на audit_events только
SELECT/INSERT (append-only; REVOKE UPDATE/DELETE — защитный явный отказ).
"""

from alembic import op
from app.core.db import Base
from app.modules.identity import models  # noqa: F401 — регистрация моделей в metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_DOMAIN_TABLES = "users, refresh_tokens, audit_subjects"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    Base.metadata.create_all(bind=op.get_bind())

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_DOMAIN_TABLES} TO cp_app")
    op.execute("GRANT SELECT, INSERT ON audit_events TO cp_app")
    # Явный отказ: append-only журнал (SECURITY §11), даже если роль получит шире
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM cp_app")


def downgrade() -> None:
    # Порядок важен: сначала таблицы (содержат citext-колонки), затем расширение.
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP EXTENSION IF EXISTS citext")
