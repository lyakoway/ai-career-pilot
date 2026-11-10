"""S1.2: миграции — цикл upgrade/downgrade, схема == DATABASE §8.1, права, каскады, citext.

Тесты sync (Alembic env сам управляет event loop); порядок внутри файла важен:
test_upgrade_downgrade_cycle оставляет базу на head для остальных PG-тестов
(pytest выполняет файлы по алфавиту: migrations -> ready -> repository).
"""

import uuid

import pytest
from alembic.config import Config

from alembic import command
from tests.conftest import fetch_all, run_write

EXPECTED_TABLES = {"users", "refresh_tokens", "audit_subjects", "audit_events", "ai_invocations"}
EXPECTED_COLUMNS = {
    "ai_invocations": {
        "id",
        "user_id",
        "purpose",
        "provider",
        "model",
        "prompt_version",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "latency_ms",
        "trace_id",
        "status",
        "created_at",
    },
    "users": {
        "id",
        "email",
        "password_hash",
        "display_name",
        "locale",
        "timezone",
        "is_active",
        "last_login_at",
        "created_at",
        "updated_at",
    },
    "refresh_tokens": {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "revoked_at",
        "user_agent",
        "ip",
    },
    "audit_subjects": {"subject_id", "user_id", "created_at"},
    "audit_events": {
        "id",
        "actor_subject_id",
        "action",
        "resource_type",
        "resource_id",
        "ip",
        "user_agent",
        "request_id",
        "context",
        "created_at",
    },
}
EXPECTED_INDEXES = {
    "idx_refresh_tokens_user_id",
    "idx_refresh_tokens_expires_at",
    "idx_users_locale",
    "idx_audit_subject_time",
    "idx_audit_action_time",
}


def _alembic_config(url: str) -> Config:
    import os

    os.environ["CP_MIGRATIONS_DATABASE_URL"] = url
    return Config("alembic.ini")


def _upgrade(url: str) -> None:
    command.upgrade(_alembic_config(url), "head")


def _downgrade(url: str) -> None:
    command.downgrade(_alembic_config(url), "base")


def _public_tables(admin: str) -> set[str]:
    rows = fetch_all(admin, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r["tablename"] for r in rows} - {"alembic_version"}


def _citext_present(admin: str) -> bool:
    rows = fetch_all(admin, "SELECT 1 AS x FROM pg_extension WHERE extname = 'citext'")
    return len(rows) == 1


def test_upgrade_downgrade_cycle(pg: dict[str, str]) -> None:
    admin, migrations = pg["admin"], pg["migrations"]

    _downgrade(migrations)  # base-состояние (после reset фикстуры — no-op)
    assert _public_tables(admin) == set()
    assert not _citext_present(admin)

    _upgrade(migrations)
    assert _public_tables(admin) == EXPECTED_TABLES
    assert _citext_present(admin)

    _upgrade(migrations)  # повторный upgrade идемпотентен
    assert _public_tables(admin) == EXPECTED_TABLES

    _downgrade(migrations)  # включая безопасное удаление citext
    assert _public_tables(admin) == set()
    assert not _citext_present(admin)

    _upgrade(migrations)  # оставить head для остальных PG-тестов
    assert _public_tables(admin) == EXPECTED_TABLES


def test_schema_matches_database_doc(pg: dict[str, str]) -> None:
    admin = pg["admin"]

    rows = fetch_all(
        admin,
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name IN "
        "('users','refresh_tokens','audit_subjects','audit_events','ai_invocations')",
    )
    columns: dict[str, set[str]] = {}
    for row in rows:
        columns.setdefault(row["table_name"], set()).add(row["column_name"])
    assert columns == EXPECTED_COLUMNS

    indexes = {
        r["indexname"]
        for r in fetch_all(admin, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    }
    assert indexes >= EXPECTED_INDEXES

    unique = {
        r["conname"]
        for r in fetch_all(
            admin,
            "SELECT conname FROM pg_constraint "
            "WHERE contype = 'u' AND connamespace = 'public'::regnamespace",
        )
    }
    assert {
        "uq_users_email",
        "uq_refresh_tokens_token_hash",
        "uq_audit_subjects_user_id",
    } <= unique

    fks = fetch_all(
        admin,
        "SELECT conname, confdeltype, conrelid::regclass::text AS tbl, "
        "confrelid::regclass::text AS ref FROM pg_constraint "
        "WHERE contype = 'f' AND connamespace = 'public'::regnamespace",
    )
    assert {r["conname"] for r in fks} == {
        "fk_refresh_tokens_user_id_users",
        "fk_audit_subjects_user_id_users",
        "ai_invocations_user_id_fkey",
    }
    # asyncpg возвращает PG-тип "char" как bytes (b"c")
    assert all(r["confdeltype"] in (b"c", "c") for r in fks)  # ON DELETE CASCADE
    assert not any(r["ref"] == "audit_events" for r in fks)  # actor_subject_id без FK

    pks = {
        (r["tbl"], r["conname"])
        for r in fetch_all(
            admin,
            "SELECT conrelid::regclass::text AS tbl, conname FROM pg_constraint "
            "WHERE contype = 'p' AND connamespace = 'public'::regnamespace",
        )
    }
    assert {
        ("users", "pk_users"),
        ("refresh_tokens", "pk_refresh_tokens"),
        ("audit_subjects", "pk_audit_subjects"),
        ("audit_events", "pk_audit_events"),
    } <= pks


def test_email_unique_case_insensitive(pg: dict[str, str]) -> None:
    admin = pg["admin"]
    run_write(
        admin,
        "INSERT INTO users (id, email, password_hash) VALUES (gen_random_uuid(), :email, 'hash')",
        {"email": "user@example.com"},
    )
    with pytest.raises(Exception, match=r"duplicate key|unique"):
        run_write(
            admin,
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (gen_random_uuid(), :email, 'hash')",
            {"email": "User@Example.com"},
        )


def test_cp_app_privileges(pg: dict[str, str]) -> None:
    admin = pg["admin"]

    def has_privilege(table: str, privilege: str) -> bool:
        rows = fetch_all(
            admin,
            "SELECT has_table_privilege('cp_app', :t, :p) AS ok",
            {"t": table, "p": privilege},
        )
        return bool(rows[0]["ok"])

    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert has_privilege("users", privilege), privilege
        assert has_privilege("refresh_tokens", privilege), privilege
        assert has_privilege("audit_subjects", privilege), privilege

    assert has_privilege("audit_events", "SELECT")
    assert has_privilege("audit_events", "INSERT")
    assert not has_privilege("audit_events", "UPDATE"), "audit_events должен быть append-only"
    assert not has_privilege("audit_events", "DELETE"), "audit_events должен быть append-only"


def test_user_deletion_cascades(pg: dict[str, str]) -> None:
    admin = pg["admin"]
    user_id = uuid.uuid4()
    run_write(
        admin,
        "INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'hash')",
        {"id": user_id, "email": f"cascade-{user_id}@example.com"},
    )
    run_write(
        admin,
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at) "
        "VALUES (gen_random_uuid(), :user_id, :h, now() + interval '1 day')",
        {"user_id": user_id, "h": "a" * 64},
    )
    run_write(
        admin,
        "INSERT INTO audit_subjects (subject_id, user_id) VALUES (gen_random_uuid(), :user_id)",
        {"user_id": user_id},
    )
    run_write(
        admin,
        "INSERT INTO audit_events (id, actor_subject_id, action) "
        "SELECT gen_random_uuid(), subject_id, 'login.success' FROM audit_subjects "
        "WHERE user_id = :user_id",
        {"user_id": user_id},
    )

    run_write(admin, "DELETE FROM users WHERE id = :user_id", {"user_id": user_id})

    assert (
        fetch_all(admin, "SELECT 1 AS x FROM refresh_tokens WHERE user_id = :u", {"u": user_id})
        == []
    )
    assert (
        fetch_all(admin, "SELECT 1 AS x FROM audit_subjects WHERE user_id = :u", {"u": user_id})
        == []
    )
    # audit_events остаются: связь уничтожена, журнал — доказательство действий
    remaining = fetch_all(
        admin,
        "SELECT actor_subject_id, action FROM audit_events "
        "WHERE actor_subject_id NOT IN (SELECT subject_id FROM audit_subjects)",
    )
    assert any(r["action"] == "login.success" for r in remaining)
