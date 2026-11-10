"""SQL-слой identity (STAGE_1_PLAN §5: только SQL, без HTTP-ошибок).

Контракты S1.5 здесь: claim_refresh — атомарный claim ротации
(UPDATE ... WHERE revoked_at IS NULL AND expires_at > now() RETURNING);
revoke_all_for_user — массовый отзыв (reuse/смена пароля);
append_audit — append-only: subject get-or-create + INSERT события.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.modules.identity.models import AuditSubject, RefreshToken, User

__all__ = [
    "append_audit",
    "claim_refresh",
    "create_refresh_session",
    "create_user",
    "get_refresh_status",
    "get_user_by_email",
    "get_user_by_id",
    "revoke_all_except",
    "revoke_all_for_user",
    "update_user",
]


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Пользователь по email (citext — сравнение без учёта регистра)."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str,
    display_name: str | None = None,
    locale: str = "ru",
    timezone: str = "Europe/Moscow",
) -> User:
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        locale=locale,
        timezone=timezone,
    )
    session.add(user)
    await session.flush()
    return user


async def claim_refresh(session: AsyncSession, token_hash: str) -> dict[str, Any] | None:
    """Атомарно отозвать токен и вернуть его данные; None — уже отозван/истёк/не существует.

    Семантика SECURITY §2: 0 затронутых строк => вызывающий классифицирует
    через get_refresh_status (revoked -> reuse; expired/unknown -> 401).
    """
    result = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        .values(revoked_at=datetime.now(UTC))
        .returning(RefreshToken.id, RefreshToken.user_id)
    )
    row = result.first()
    if row is None:
        return None
    return {"id": row.id, "user_id": row.user_id}


async def get_refresh_status(session: AsyncSession, token_hash: str) -> dict[str, Any]:
    """Классификация токена после неудачного claim (S1.5): expired | revoked | missing.

    Возвращает {"status": ..., "user_id": uuid | None}; для missing user_id=None.
    """
    result = await session.execute(
        select(
            RefreshToken.user_id,
            RefreshToken.revoked_at,
            RefreshToken.expires_at,
        ).where(RefreshToken.token_hash == token_hash)
    )
    row = result.first()
    if row is None:
        return {"status": "missing", "user_id": None}
    if row.revoked_at is not None:
        return {"status": "revoked", "user_id": row.user_id}
    if row.expires_at <= datetime.now(UTC):
        return {"status": "expired", "user_id": row.user_id}
    return {"status": "active", "user_id": row.user_id}


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Отозвать все активные refresh-сессии пользователя; вернуть количество."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    # rowcount есть только у DML-результатов; для UPDATE без RETURNING это число строк
    return int(getattr(result, "rowcount", 0) or 0)


async def create_refresh_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Создать refresh-сессию (токен хранится только как sha256-хеш)."""
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
    )
    await session.flush()


async def revoke_all_except(
    session: AsyncSession, user_id: uuid.UUID, keep_token_hash: str | None
) -> int:
    """Отозвать все активные сессии, кроме указанной (смена пароля, SECURITY §9.2)."""
    statement = update(RefreshToken).where(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None),
        RefreshToken.token_hash != keep_token_hash,
    )
    result = await session.execute(statement.values(revoked_at=datetime.now(UTC)))
    return int(getattr(result, "rowcount", 0) or 0)


async def update_user(session: AsyncSession, user: User, **changes: Any) -> User:
    """Изменить поля пользователя (только переданные); flush без commit.

    refresh обязателен: onupdate/server_default помечают атрибуты expired,
    а ленивая догрузка в async-контексте невозможна (MissingGreenlet).
    """
    for field, value in changes.items():
        setattr(user, field, value)
    await session.flush()
    await session.refresh(user)
    return user


async def get_or_create_subject(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """subject_id пользователя; при первом событии — случайный UUIDv4 (DATABASE §3.15).

    Конкурентное создание первого subject'а безопасно: ON CONFLICT DO NOTHING +
    повторное чтение.
    """
    existing = await session.execute(
        select(AuditSubject.subject_id).where(AuditSubject.user_id == user_id)
    )
    subject_id = existing.scalar_one_or_none()
    if subject_id is not None:
        return subject_id

    result = await session.execute(
        text(
            "INSERT INTO audit_subjects (subject_id, user_id) "
            "VALUES (gen_random_uuid(), :user_id) "
            "ON CONFLICT (user_id) DO NOTHING RETURNING subject_id"
        ),
        {"user_id": user_id},
    )
    subject_id = result.scalar_one_or_none()
    if subject_id is None:
        existing = await session.execute(
            select(AuditSubject.subject_id).where(AuditSubject.user_id == user_id)
        )
        subject_id = existing.scalar_one()
    return subject_id


async def append_audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Append-only запись аудита; вызывается в транзакции действия (SECURITY §11)."""
    subject_id: uuid.UUID | None = None
    if user_id is not None:
        subject_id = await get_or_create_subject(session, user_id)
    await session.execute(
        text(
            "INSERT INTO audit_events "
            "(id, actor_subject_id, action, resource_type, resource_id, "
            " ip, user_agent, request_id, context) "
            "VALUES (:id, :subject_id, :action, :resource_type, :resource_id, "
            " :ip, :user_agent, :request_id, CAST(:context AS jsonb))"
        ),
        {
            "id": uuid7(),  # PK — uuid7 app-side (DATABASE §1)
            "subject_id": subject_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip": ip,
            "user_agent": user_agent,
            "request_id": request_id,
            "context": _json(context),
        },
    )


def _json(value: dict[str, Any] | None) -> str | None:
    return json.dumps(value) if value is not None else None
