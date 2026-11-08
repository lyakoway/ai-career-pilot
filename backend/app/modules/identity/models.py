"""SQLAlchemy-модели identity: users, refresh_tokens, audit_subjects, audit_events.

Схема — строго DATABASE.md §3.1 и §3.15 (STAGE_1_PLAN §8.1).
Инварианты: audit_events append-only (права роли cp_app — только SELECT/INSERT,
выдаются миграцией 0001); actor_subject_id — намеренно БЕЗ FK, чтобы удаление
пользователя (каскад audit_subjects) не мутировало и не блокировало журнал.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.core.db import Base


def _uuid7() -> uuid.UUID:
    return uuid7()


def _uuid4() -> uuid.UUID:
    # случайный, не выводимый из user_id (SECURITY §11, DATABASE §3.15)
    return uuid.uuid4()


class User(Base):
    """Учётная запись; основа multi-tenancy."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid7)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, server_default="ru")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Europe/Moscow")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RefreshToken(Base):
    """Refresh-сессия: opaque-токен хранится только как sha256-хеш."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)


Index("idx_refresh_tokens_user_id", RefreshToken.user_id)
Index("idx_refresh_tokens_expires_at", RefreshToken.expires_at)
Index("idx_users_locale", User.locale)


class AuditSubject(Base):
    """Единственная связь аудита с пользователем; уничтожается при удалении пользователя."""

    __tablename__ = "audit_subjects"

    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    """Append-only журнал безопасности; actor_subject_id без FK (осознанно)."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid7)
    actor_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,  # без FK: см. docstring модуля
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index("idx_audit_subject_time", AuditEvent.actor_subject_id, AuditEvent.created_at.desc())
Index("idx_audit_action_time", AuditEvent.action, AuditEvent.created_at.desc())
