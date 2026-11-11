"""SQL-слой resume_intelligence: резюме и факты профиля (DATABASE §3.3).

Q-8 (delete_resume): confirmed-факты переживают удаление резюме —
перенос resume_id=NULL выполняется ДО DELETE (порядок операций, не схема).
"""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.resume_intelligence.models import ProfileFact, Resume

__all__ = [
    "create_fact",
    "create_resume",
    "delete_resume",
    "get_by_id",
    "get_fact_by_id",
    "list_facts",
    "list_resumes",
    "supersede_fact",
    "update_fact",
]


# ---------- resumes ----------


async def get_by_id(
    session: AsyncSession, *, user_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume | None:
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_resumes(session: AsyncSession, user_id: uuid.UUID) -> list[Resume]:
    result = await session.execute(
        select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc())
    )
    return list(result.scalars().all())


async def create_resume(session: AsyncSession, **fields: Any) -> Resume:
    resume = Resume(**fields)
    session.add(resume)
    await session.flush()
    return resume


async def delete_resume(session: AsyncSession, *, user_id: uuid.UUID, resume_id: uuid.UUID) -> int:
    """Удалить резюме; confirmed-факты переносятся (resume_id=NULL) до DELETE.

    Возвращает число перенесённых confirmed-фактов (для логов/audit).
    """
    transferred = await session.execute(
        update(ProfileFact)
        .where(
            ProfileFact.resume_id == resume_id,
            ProfileFact.status == "confirmed",
        )
        .values(resume_id=None)
    )
    transferred_count = int(getattr(transferred, "rowcount", 0) or 0)
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        return transferred_count
    await session.delete(resume)
    await session.flush()
    return transferred_count


# ---------- profile_facts ----------


async def get_fact_by_id(
    session: AsyncSession, *, user_id: uuid.UUID, fact_id: uuid.UUID
) -> ProfileFact | None:
    result = await session.execute(
        select(ProfileFact).where(ProfileFact.id == fact_id, ProfileFact.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_facts(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    fact_type: str | None = None,
    status: str | None = None,
) -> list[ProfileFact]:
    statement = select(ProfileFact).where(ProfileFact.career_profile_id == profile_id)
    if fact_type is not None:
        statement = statement.where(ProfileFact.fact_type == fact_type)
    if status is not None:
        statement = statement.where(ProfileFact.status == status)
    statement = statement.order_by(ProfileFact.created_at)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def create_fact(session: AsyncSession, **fields: Any) -> ProfileFact:
    fact = ProfileFact(**fields)
    session.add(fact)
    await session.flush()
    return fact


async def update_fact(session: AsyncSession, fact: ProfileFact, **changes: Any) -> ProfileFact:
    """Правки подтверждения/редактирования; superseded-цепочку ведёт service (S2.4/2.5)."""
    for field_name, value in changes.items():
        setattr(fact, field_name, value)
    await session.flush()
    await session.refresh(fact)
    return fact


async def supersede_fact(
    session: AsyncSession, fact: ProfileFact, replacement_id: uuid.UUID
) -> ProfileFact:
    """Пометить факт superseded с указанием замены (STAGE_2_PLAN §4.7)."""
    fact.status = "superseded"
    fact.superseded_by = replacement_id
    await session.flush()
    await session.refresh(fact)
    return fact


async def count_draft_facts(session: AsyncSession, resume_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ProfileFact)
        .where(ProfileFact.resume_id == resume_id, ProfileFact.status == "draft")
    )
    return int(result.scalar_one())
