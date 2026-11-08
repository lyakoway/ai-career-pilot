"""S1.2: SQL-слой identity на реальной PostgreSQL — под ролью cp_app (реальные GRANT'ы)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.modules.identity import repository as repo


async def _insert_refresh_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    token_hash: str,
    *,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> None:
    await session.execute(
        text(
            "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked_at) "
            "VALUES (:id, :user_id, :token_hash, :expires_at, :revoked_at)"
        ),
        {
            "id": uuid7(),
            "user_id": user_id,
            "token_hash": token_hash,
            "expires_at": expires_at or datetime.now(UTC) + timedelta(days=30),
            "revoked_at": datetime.now(UTC) if revoked else None,
        },
    )


async def test_create_user_and_case_insensitive_email(db_session: AsyncSession) -> None:
    user = await repo.create_user(db_session, email="Owner@Example.com", password_hash="h")
    assert uuid.UUID(str(user.id)).version == 7  # PK uuid7 app-side (DATABASE §1)

    found = await repo.get_user_by_email(db_session, "owner@example.com")
    assert found is not None and found.id == user.id

    missing = await repo.get_user_by_email(db_session, "other@example.com")
    assert missing is None


async def test_claim_refresh_success_then_none(db_session: AsyncSession) -> None:
    user = await repo.create_user(db_session, email="a@example.com", password_hash="h")
    token_hash = "b" * 64
    await _insert_refresh_token(db_session, user.id, token_hash)

    claimed = await repo.claim_refresh(db_session, token_hash)
    assert claimed is not None
    assert claimed["user_id"] == user.id

    # Второй claim тем же токеном -> 0 строк (основание reuse-детекции S1.5)
    assert await repo.claim_refresh(db_session, token_hash) is None


async def test_claim_refresh_expired_or_revoked_is_none(db_session: AsyncSession) -> None:
    user = await repo.create_user(db_session, email="b@example.com", password_hash="h")
    expired_hash, revoked_hash = "c" * 64, "d" * 64
    await _insert_refresh_token(
        db_session,
        user.id,
        expired_hash,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await _insert_refresh_token(db_session, user.id, revoked_hash, revoked=True)

    assert await repo.claim_refresh(db_session, expired_hash) is None
    assert await repo.claim_refresh(db_session, revoked_hash) is None
    assert await repo.claim_refresh(db_session, "e" * 64) is None  # не существует


async def test_revoke_all_for_user(db_session: AsyncSession) -> None:
    user = await repo.create_user(db_session, email="r@example.com", password_hash="h")
    other = await repo.create_user(db_session, email="r2@example.com", password_hash="h")
    await _insert_refresh_token(db_session, user.id, "1" * 64)
    await _insert_refresh_token(db_session, user.id, "2" * 64)
    await _insert_refresh_token(db_session, user.id, "3" * 64, revoked=True)
    await _insert_refresh_token(db_session, other.id, "4" * 64)

    revoked_count = await repo.revoke_all_for_user(db_session, user.id)
    assert revoked_count == 2  # только активные; уже отозванный не считается

    rows = await db_session.execute(
        text("SELECT token_hash FROM refresh_tokens WHERE user_id = :u AND revoked_at IS NOT NULL"),
        {"u": user.id},
    )
    assert {r[0] for r in rows} == {"1" * 64, "2" * 64, "3" * 64}
    assert await repo.revoke_all_for_user(db_session, user.id) == 0


async def test_append_audit_reuses_subject(db_session: AsyncSession) -> None:
    user = await repo.create_user(db_session, email="au@example.com", password_hash="h")

    await repo.append_audit(
        db_session,
        user_id=user.id,
        action="user.provisioned",
        resource_type="user",
        resource_id=user.id,
        context={"via": "cli"},
    )
    await repo.append_audit(db_session, user_id=user.id, action="login.success")

    subjects = await db_session.execute(
        text("SELECT subject_id FROM audit_subjects WHERE user_id = :u"), {"u": user.id}
    )
    subject_ids = [r[0] for r in subjects]
    assert len(subject_ids) == 1

    events = await db_session.execute(
        text(
            "SELECT actor_subject_id, action, context FROM audit_events "
            "WHERE actor_subject_id = :s ORDER BY created_at, id"
        ),
        {"s": subject_ids[0]},
    )
    rows = [(r[0], r[1], r[2]) for r in events]
    assert len(rows) == 2
    assert rows[0][1] == "user.provisioned" and rows[0][2] == {"via": "cli"}
    assert rows[1][1] == "login.success"

    subject = await repo.get_or_create_subject(db_session, user.id)
    assert subject == subject_ids[0]


async def test_audit_events_update_denied_for_cp_app(db_session: AsyncSession) -> None:
    """append-only под реальной ролью: UPDATE/DELETE audit_events -> permission error."""
    user = await repo.create_user(db_session, email="deny@example.com", password_hash="h")
    await repo.append_audit(db_session, user_id=user.id, action="login.failed")

    with pytest.raises(Exception, match="permission"):
        await db_session.execute(text("UPDATE audit_events SET action = 'tampered'"))
    # первая ошибка абортит транзакцию — откатываем перед второй попыткой
    await db_session.rollback()

    with pytest.raises(Exception, match="permission"):
        await db_session.execute(text("DELETE FROM audit_events"))
