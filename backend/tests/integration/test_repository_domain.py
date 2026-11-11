"""S2.1 integration: репозитории домена — tenant, ensure_default, activate, delete_resume."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import create_db_engine, create_session_factory
from app.modules.career_profile import repository as profiles
from app.modules.resume_intelligence import repository as resumes_repo


@pytest.fixture
def session_factory(pg: dict[str, str]):
    engine = create_db_engine(pg["app"])
    try:
        yield create_session_factory(engine)
    finally:
        asyncio.run(engine.dispose())


async def _two_users_with_profiles(
    session_factory,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    async with session_factory() as session:
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        for uid, email in ((user_a, "ra@example.com"), (user_b, "rb@example.com")):
            await session.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'h')"),
                {"id": uid, "e": email},
            )
        profile_a = await profiles.create(session, user_id=user_a, name="A")
        profile_b = await profiles.create(session, user_id=user_b, name="B")
        await session.commit()
    return user_a, user_b, profile_a.id, profile_b.id


async def test_tenant_isolation_all_methods(session_factory) -> None:
    user_a, user_b, profile_a, profile_b = await _two_users_with_profiles(session_factory)

    async with session_factory() as session:
        # чужой профиль → None
        assert await profiles.get_by_id(session, user_id=user_a, profile_id=profile_b) is None
        assert await profiles.get_by_id(session, user_id=user_b, profile_id=profile_a) is None
        # list только свои
        assert [p.id for p in await profiles.list_by_user(session, user_a)] == [profile_a]

        # резюме/факты: чужой id → None
        resume = await resumes_repo.create_resume(
            session,
            user_id=user_a,
            career_profile_id=profile_a,
            file_key=f"users/{user_a}/resumes/{uuid.uuid4()}/cv.pdf",
            checksum_sha256="b" * 64,
        )
        await session.commit()

    async with session_factory() as session:
        assert await resumes_repo.get_by_id(session, user_id=user_b, resume_id=resume.id) is None
        assert (
            await resumes_repo.get_by_id(session, user_id=user_a, resume_id=resume.id) is not None
        )
        fact = await resumes_repo.create_fact(
            session,
            user_id=user_a,
            career_profile_id=profile_a,
            resume_id=resume.id,
            fact_type="skill",
            payload={"skill": "Python"},
            source="user_input",
        )
        await session.commit()

    async with session_factory() as session:
        assert await resumes_repo.get_fact_by_id(session, user_id=user_b, fact_id=fact.id) is None
        facts_b = await resumes_repo.list_facts(session, profile_id=profile_b)
        assert facts_b == []
        facts_a = await resumes_repo.list_facts(session, profile_id=profile_a)
        assert [f.id for f in facts_a] == [fact.id]


async def test_ensure_default_creates_once(session_factory) -> None:
    user_id = uuid.uuid4()
    async with session_factory() as session:
        await session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'h')"),
            {"id": user_id, "e": "def@example.com"},
        )
        default = await profiles.ensure_default(session, user_id)
        await session.commit()

    async with session_factory() as session:
        again = await profiles.ensure_default(session, user_id)
        assert again.id == default.id  # повтор → тот же профиль
        all_profiles = await profiles.list_by_user(session, user_id)
        assert len(all_profiles) == 1
        assert default.name == "Default"
        assert default.is_active is False  # до явной activate (Q-1)


async def test_activate_atomic_single_active(session_factory) -> None:
    async with session_factory() as session:
        user_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'h')"),
            {"id": user_id, "e": "act@example.com"},
        )
        p1 = await profiles.create(session, user_id=user_id, name="one")
        p2 = await profiles.create(session, user_id=user_id, name="two")
        await profiles.activate(session, user_id=user_id, profile_id=p1.id)
        await session.commit()

    async with session_factory() as session:
        p1_db = await profiles.get_by_id(session, user_id=user_id, profile_id=p1.id)
        await profiles.get_by_id(session, user_id=user_id, profile_id=p2.id)
        assert p1_db.is_active is True and p1_db.activated_at is not None

        # переключение на p2: p1 деактивируется атомарно
        await profiles.activate(session, user_id=user_id, profile_id=p2.id)
        await session.commit()

    async with session_factory() as session:
        active = [p for p in await profiles.list_by_user(session, user_id) if p.is_active]
        assert len(active) == 1 and active[0].id == p2.id


async def test_update_fields_increments_version(session_factory) -> None:
    async with session_factory() as session:
        user_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'h')"),
            {"id": user_id, "e": "ver@example.com"},
        )
        profile = await profiles.create(session, user_id=user_id, name="v")
        await session.commit()

    async with session_factory() as session:
        profile = await profiles.get_by_id(session, user_id=user_id, profile_id=profile.id)
        updated = await profiles.update_fields(session, profile, salary_min=250000)
        assert updated.version == 2
        assert updated.salary_min == 250000
        await session.commit()


async def test_delete_resume_confirmed_survive_draft_cascade(session_factory, pg) -> None:
    """Q-8: confirmed переносятся (resume_id=NULL), draft — каскад удаляется."""
    async with session_factory() as session:
        user_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'h')"),
            {"id": user_id, "e": "del@example.com"},
        )
        profile = await profiles.create(session, user_id=user_id, name="D")
        resume = await resumes_repo.create_resume(
            session,
            user_id=user_id,
            career_profile_id=profile.id,
            file_key=f"users/{user_id}/resumes/{uuid.uuid4()}/cv.pdf",
            checksum_sha256="c" * 64,
        )
        confirmed = await resumes_repo.create_fact(
            session,
            user_id=user_id,
            career_profile_id=profile.id,
            resume_id=resume.id,
            fact_type="skill",
            payload={"skill": "Python"},
            source="llm_extraction",
            evidence=[{"quote": "Senior Python developer", "section": "skills"}],
            status="confirmed",
            verified=True,
        )
        draft = await resumes_repo.create_fact(
            session,
            user_id=user_id,
            career_profile_id=profile.id,
            resume_id=resume.id,
            fact_type="skill",
            payload={"skill": "Rust"},
            source="llm_extraction",
            evidence=[{"quote": "learning Rust", "section": "skills"}],
        )
        await session.commit()

    async with session_factory() as session:
        transferred = await resumes_repo.delete_resume(
            session, user_id=user_id, resume_id=resume.id
        )
        await session.commit()

    assert transferred == 1

    rows = await _fetch_all_raw(pg, "SELECT id, resume_id, status FROM profile_facts")
    by_id = {row[0]: (row[1], row[2]) for row in rows}
    assert by_id[confirmed.id] == (None, "confirmed")  # пережил, отвязан от файла
    assert draft.id not in by_id  # draft каскадом удалён


async def _fetch_all_raw(pg: dict[str, str], sql: str) -> list[Any]:
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            return result.all()
    finally:
        await engine.dispose()
