"""S2.0 integration (реальная PG): ai_invocations — append-only + CASCADE + запись."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import create_db_engine, create_session_factory
from app.llm.invocations import record_invocation
from tests.conftest import fetch_all


async def _exec_admin(pg: dict[str, str], sql: str, params: dict[str, Any]) -> None:
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _fetch_admin(
    pg: dict[str, str], sql: str, params: dict[str, Any] | None = None
) -> list[Any]:
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.all()
    finally:
        await engine.dispose()


async def _seed_user(pg: dict[str, str], email: str) -> str:
    user_id = uuid.uuid4()
    await _exec_admin(
        pg,
        "INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'hash')",
        {"id": user_id, "email": email},
    )
    return str(user_id)


async def _record(
    pg: dict[str, str], user_id: uuid.UUID | None, purpose: str = "resume_parse"
) -> uuid.UUID:
    engine = create_db_engine(pg["app"])
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            invocation_id = await record_invocation(
                session,
                user_id=user_id,
                purpose=purpose,
                provider="mock",
                model="mock-extractor-v1",
                prompt_version="resume_extraction.v1",
                tokens_in=100,
                tokens_out=50,
                cost_usd=Decimal("0.000000"),
                latency_ms=42,
                status="ok",
            )
            await session.commit()
        return invocation_id
    finally:
        await engine.dispose()


async def test_record_invocation_insert_and_fields(pg: dict[str, str]) -> None:
    user_id = await _seed_user(pg, "inv@example.com")
    invocation_id = await _record(pg, uuid.UUID(user_id))
    rows = await _fetch_admin(
        pg,
        "SELECT purpose, provider, model, tokens_in, tokens_out, latency_ms, status "
        "FROM ai_invocations WHERE id = :id",
        {"id": invocation_id},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.purpose == "resume_parse"
    assert row.provider == "mock" and row.model == "mock-extractor-v1"
    assert (row.tokens_in, row.tokens_out, row.latency_ms, row.status) == (100, 50, 42, "ok")


async def test_system_invocation_without_user(pg: dict[str, str]) -> None:
    """user_id NULL — системные/eval вызовы (DATABASE §3.12)."""
    invocation_id = await _record(pg, None, purpose="evaluation")
    rows = await _fetch_admin(
        pg,
        "SELECT user_id, purpose FROM ai_invocations WHERE id = :id",
        {"id": invocation_id},
    )
    assert rows[0].user_id is None and rows[0].purpose == "evaluation"


def test_cp_app_privileges_append_only(pg: dict[str, str]) -> None:
    admin = pg["admin"]

    def has_privilege(privilege: str) -> bool:
        rows = fetch_all(
            admin,
            "SELECT has_table_privilege('cp_app', 'ai_invocations', :p) AS ok",
            {"p": privilege},
        )
        return bool(rows[0]["ok"])

    assert has_privilege("SELECT") and has_privilege("INSERT")
    assert not has_privilege("UPDATE"), "ai_invocations должен быть append-only"
    assert not has_privilege("DELETE"), "ai_invocations должен быть append-only"


async def test_update_denied_for_cp_app(pg: dict[str, str]) -> None:
    user_id = await _seed_user(pg, "deny-inv@example.com")
    invocation_id = await _record(pg, uuid.UUID(user_id))
    engine = create_db_engine(pg["app"])
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            with pytest.raises(Exception, match="permission"):
                await session.execute(
                    text("UPDATE ai_invocations SET status = 'tampered' WHERE id = :id"),
                    {"id": invocation_id},
                )
    finally:
        await engine.dispose()


async def test_user_deletion_cascades_invocations(pg: dict[str, str]) -> None:
    """DATABASE §3.12: user_id FK CASCADE — инвокации привязаны к пользователю
    (в отличие от audit_events, которые переживают удаление)."""
    user_id = await _seed_user(pg, "cascade-inv@example.com")
    await _record(pg, uuid.UUID(user_id))
    await _exec_admin(pg, "DELETE FROM users WHERE id = :id", {"id": uuid.UUID(user_id)})
    rows = await _fetch_admin(
        pg,
        "SELECT 1 FROM ai_invocations WHERE user_id = :id",
        {"id": uuid.UUID(user_id)},
    )
    assert rows == []


def test_schema_ai_invocations(pg: dict[str, str]) -> None:
    """Миграция 0002: колонки и индексы по DATABASE §3.12."""
    columns = {
        r["column_name"]
        for r in fetch_all(
            pg["admin"],
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_invocations'",
        )
    }
    assert {
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
    } <= columns
    indexes = {
        r["indexname"]
        for r in fetch_all(
            pg["admin"],
            "SELECT indexname FROM pg_indexes WHERE tablename = 'ai_invocations'",
        )
    }
    assert {"idx_ai_invocations_user_time", "idx_ai_invocations_purpose_time"} <= indexes
