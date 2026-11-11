"""identity: users, refresh_tokens, audit_subjects, audit_events + роли-гранты

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-08

Схема — DATABASE.md §3.1, §3.15 (STAGE_1_PLAN §8.1).
Роли cp_app/cp_migrations создаются инфраструктурным bootstrap'ом (docker/db/bootstrap.sql),
миграция выдаёт GRANT: cp_app — DML на доменные таблицы, на audit_events только
SELECT/INSERT (append-only; REVOKE UPDATE/DELETE — защитный явный отказ).

S2.1-фикс: явные CREATE/DROP вместо Base.metadata.create_all/drop_all —
metadata растёт с этапами, архивная миграция не должна подхватывать чужие
таблицы (проявилось при добавлении 0003_domain).
"""

import uuid as _uuid  # noqa: F401 — uuid7 генерится на уровне приложения

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_DOMAIN_TABLES = "users, refresh_tokens, audit_subjects"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute(
        """
        CREATE TABLE users (
            id            uuid CONSTRAINT pk_users PRIMARY KEY,
            email         citext NOT NULL,
            password_hash text NOT NULL,
            display_name  text,
            locale        varchar(8) NOT NULL DEFAULT 'ru',
            timezone      text NOT NULL DEFAULT 'Europe/Moscow',
            is_active     boolean NOT NULL DEFAULT true,
            last_login_at timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email ON users (email)")
    op.execute("CREATE INDEX idx_users_locale ON users (locale)")
    op.execute(
        """
        CREATE TABLE refresh_tokens (
            id         uuid CONSTRAINT pk_refresh_tokens PRIMARY KEY,
            user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash varchar(64) NOT NULL,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            user_agent text,
            ip         inet,
            CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
            CONSTRAINT fk_refresh_tokens_user_id_users
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens (user_id)")
    op.execute("CREATE INDEX idx_refresh_tokens_expires_at ON refresh_tokens (expires_at)")
    op.execute(
        """
        CREATE TABLE audit_subjects (
            subject_id uuid CONSTRAINT pk_audit_subjects PRIMARY KEY,
            user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_audit_subjects_user_id UNIQUE (user_id),
            CONSTRAINT fk_audit_subjects_user_id_users
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_events (
            id               uuid CONSTRAINT pk_audit_events PRIMARY KEY,
            actor_subject_id uuid,
            action           text NOT NULL,
            resource_type    text,
            resource_id      uuid,
            ip               inet,
            user_agent       text,
            request_id       text,
            context          jsonb,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_audit_subject_time ON audit_events (actor_subject_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_audit_action_time ON audit_events (action, created_at DESC)")

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_DOMAIN_TABLES} TO cp_app")
    op.execute("GRANT SELECT, INSERT ON audit_events TO cp_app")
    # Явный отказ: append-only журнал (SECURITY §11), даже если роль получит шире
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM cp_app")


def downgrade() -> None:
    # Порядок важен: сначала таблицы (содержат citext-колонки), затем расширение.
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS audit_subjects")
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP EXTENSION IF EXISTS citext")
