"""SQL-слой career_profile (STAGE_2_PLAN §5): только SQL, без HTTP-ошибок.

Tenant-изоляция: get_by_id(user_id, id) — чужой id → None (SECURITY §3).
activate — атомарный (UPDATE ... WHERE is_active против partial unique).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.career_profile.models import CareerProfile

__all__ = [
    "activate",
    "create",
    "delete",
    "ensure_default",
    "get_by_id",
    "list_by_user",
    "update",
]


async def get_by_id(
    session: AsyncSession, *, user_id: uuid.UUID, profile_id: uuid.UUID
) -> CareerProfile | None:
    result = await session.execute(
        select(CareerProfile).where(
            CareerProfile.id == profile_id, CareerProfile.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def list_by_user(session: AsyncSession, user_id: uuid.UUID) -> list[CareerProfile]:
    result = await session.execute(
        select(CareerProfile)
        .where(CareerProfile.user_id == user_id)
        .order_by(CareerProfile.created_at)
    )
    return list(result.scalars().all())


async def get_first_by_user(session: AsyncSession, user_id: uuid.UUID) -> CareerProfile | None:
    """Первый (по created_at) профиль пользователя — для фактов при загрузке резюме."""
    result = await session.execute(
        select(CareerProfile)
        .where(CareerProfile.user_id == user_id)
        .order_by(CareerProfile.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create(session: AsyncSession, *, user_id: uuid.UUID, **fields: object) -> CareerProfile:
    profile = CareerProfile(user_id=user_id, **fields)
    session.add(profile)
    await session.flush()
    return profile


async def ensure_default(session: AsyncSession, user_id: uuid.UUID) -> CareerProfile:
    """Q-1: первый профиль пользователя; если уже есть — вернуть его (Default не дублируется)."""
    existing = await get_first_by_user(session, user_id)
    if existing is not None:
        return existing
    return await create(session, user_id=user_id, name="Default")


async def update_fields(
    session: AsyncSession, profile: CareerProfile, **changes: object
) -> CareerProfile:
    """Изменить поля + инкремент version (STAGE_1-аналог воспроизводимости)."""
    for field_name, value in changes.items():
        setattr(profile, field_name, value)
    profile.version = profile.version + 1
    await session.flush()
    await session.refresh(profile)
    return profile


async def activate(session: AsyncSession, *, user_id: uuid.UUID, profile_id: uuid.UUID) -> None:
    """Атомарно: деактивировать все, активировать целевой (partial unique держит инвариант)."""
    now = datetime.now(UTC)
    await session.execute(
        update(CareerProfile)
        .where(CareerProfile.user_id == user_id, CareerProfile.is_active.is_(True))
        .values(is_active=False)
    )
    await session.execute(
        update(CareerProfile)
        .where(CareerProfile.id == profile_id, CareerProfile.user_id == user_id)
        .values(is_active=True, activated_at=now)
    )
    await session.flush()


async def delete(session: AsyncSession, profile: CareerProfile) -> None:
    await session.delete(profile)
    await session.flush()
