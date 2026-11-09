"""Сценарии identity: регистрация, вход, смена пароля (STAGE_1_PLAN §4.1, §9.2).

Транзакционная граница — get_session (commit при успехе HTTP-запроса);
audit login.failed пишется ОТДЕЛЬНОЙ транзакцией (не должен откатиться
вместе с 401-ответом).
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AuthError, ConflictError, ValidationError
from app.core.logging import bind_user_id
from app.core.security.jwt import encode_access
from app.core.security.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.modules.identity import repository
from app.modules.identity.models import User

__all__ = [
    "AuthTokens",
    "InvalidCredentialsError",
    "audit_failed_login",
    "change_password",
    "issue_access_token",
    "login",
    "new_refresh_token",
    "register_user",
]

_REFRESH_PREFIX = "rt_"


class InvalidCredentialsError(Exception):
    """Неверный email или пароль (детали не раскрываются — SECURITY §2)."""


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str  # уходит ТОЛЬКО в httpOnly-cookie, не в JSON


def new_refresh_token() -> tuple[str, str]:
    """(opaque-токен `rt_<43 base64url>`, sha256-хеш) — SECURITY §2."""
    token = _REFRESH_PREFIX + secrets.token_urlsafe(32)
    return token, _sha256(token)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_access_token(settings: Settings, user: User) -> str:
    return encode_access(
        user.id, user.locale, settings.access_ttl_seconds, secret=settings.jwt_secret
    )


async def register_user(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    locale: str,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> tuple[User, AuthTokens]:
    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        raise ValidationError(str(exc)) from exc

    if await repository.get_user_by_email(session, email) is not None:
        raise ConflictError

    user = await repository.create_user(
        session, email=email, password_hash=hash_password(password), locale=locale
    )
    tokens = await _issue_refresh(session, settings, user, ip=ip, user_agent=user_agent)
    await repository.append_audit(
        session,
        user_id=user.id,
        action="user.registered",
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
        context={"via": "api"},
    )
    return user, tokens


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> tuple[User, AuthTokens]:
    user = await repository.get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        # Одинаковая ошибка для неверного пароля и несуществующего email
        raise InvalidCredentialsError
    assert user is not None

    # Ротация параметров Argon2 при входе (needs_rehash из S1.3)
    if needs_rehash(user.password_hash):
        await repository.update_user(session, user, password_hash=hash_password(password))

    tokens = await _issue_refresh(session, settings, user, ip=ip, user_agent=user_agent)
    await repository.update_user(session, user, last_login_at=datetime.now(UTC))
    await repository.append_audit(
        session,
        user_id=user.id,
        action="login.success",
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
    )
    bind_user_id(str(user.id))
    return user, tokens


async def audit_failed_login(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> None:
    """Отдельная транзакция: audit login.failed переживает rollback 401-ответа.

    user_id — только если email существует (subject); без PII в context.
    """
    async with session_factory() as session:
        user = await repository.get_user_by_email(session, email)
        await repository.append_audit(
            session,
            user_id=user.id if user is not None else None,
            action="login.failed",
            ip=ip,
            user_agent=user_agent,
            request_id=request_id,
        )
        await session.commit()


async def change_password(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    old_password: str,
    new_password: str,
    current_refresh_token: str | None,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> tuple[str, int]:
    """Смена пароля (STAGE_1_PLAN §9.2): verify старого -> hash нового ->
    отзыв всех сессий, КРОМЕ текущей (если refresh предъявлен) -> свежий access."""
    if not verify_password(old_password, user.password_hash):
        raise AuthError
    try:
        validate_password(new_password)
    except PasswordPolicyError as exc:
        raise ValidationError(str(exc)) from exc

    await repository.update_user(session, user, password_hash=hash_password(new_password))

    keep_hash = _sha256(current_refresh_token) if current_refresh_token else None
    revoked = await repository.revoke_all_except(session, user.id, keep_hash)

    await repository.append_audit(
        session,
        user_id=user.id,
        action="password.changed",
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
        context={"sessionsRevoked": revoked},
    )
    return issue_access_token(settings, user), revoked


async def _issue_refresh(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
) -> AuthTokens:
    refresh_token, token_hash = new_refresh_token()
    await repository.create_refresh_session(
        session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_ttl_days),
        ip=ip,
        user_agent=user_agent,
    )
    return AuthTokens(
        access_token=issue_access_token(settings, user),
        refresh_token=refresh_token,
    )
