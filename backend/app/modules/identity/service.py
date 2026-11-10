"""Сценарии identity: регистрация, вход, смена пароля (STAGE_1_PLAN §4.1, §9.2).

Транзакционная граница — get_session (commit при успехе HTTP-запроса);
audit login.failed пишется ОТДЕЛЬНОЙ транзакцией (не должен откатиться
вместе с 401-ответом).
"""

import hashlib
import secrets
import uuid
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
    "RefreshReuseDetectedError",
    "audit_failed_login",
    "change_password",
    "handle_reuse",
    "issue_access_token",
    "login",
    "logout",
    "new_refresh_token",
    "register_user",
    "rotate_refresh",
]

_REFRESH_PREFIX = "rt_"


class InvalidCredentialsError(Exception):
    """Неверный email или пароль (детали не раскрываются — SECURITY §2)."""


class RefreshReuseDetectedError(Exception):
    """Повторное использование отозванного refresh-токена (SECURITY §2, S1.5 AC)."""


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


async def rotate_refresh(
    session: AsyncSession,
    settings: Settings,
    *,
    refresh_token: str,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> AuthTokens:
    """Ротация refresh (STAGE_1_PLAN §9.1, S1.5): атомарный claim -> новая пара.

    claim=0 строк -> классификация через get_refresh_status:
      expired/missing -> InvalidCredentialsError (без массового отзыва);
      revoked -> RefreshReuseDetectedError (вызывающий вызывает handle_reuse
      с ОТДЕЛЬНОЙ транзакцией: массовый отзыв + audit, AC ⑥/⑦).
    """
    token_hash = _sha256(refresh_token)
    claimed = await repository.claim_refresh(session, token_hash)

    if claimed is None:
        status = await repository.get_refresh_status(session, token_hash)
        if status["status"] == "revoked":
            raise RefreshReuseDetectedError
        # expired / missing: одинаковый 401 без mass-revoke
        raise InvalidCredentialsError

    user = await repository.get_user_by_id(session, claimed["user_id"])
    if user is None or not user.is_active:
        # Пользователь удалён/деактивирован после выдачи токена
        raise InvalidCredentialsError

    tokens = await _issue_refresh(session, settings, user, ip=ip, user_agent=user_agent)
    await repository.append_audit(
        session,
        user_id=user.id,
        action="refresh.rotated",
        resource_type="refresh_token",
        resource_id=claimed["id"],
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
    )
    return tokens


async def handle_reuse(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> None:
    """Reuse (AC ⑥/⑧): массовый отзыв + audit refresh.reuse_detected ОДНОЙ
    транзакцией, коммит ДО 401-ответа (stateless-транзакция запроса откатится).
    При исключении — rollback: ничего частичного не остаётся (AC ⑦).
    """
    async with session_factory() as session:
        try:
            revoked = await repository.revoke_all_for_user(session, user_id)
            await repository.append_audit(
                session,
                user_id=user_id,
                action="refresh.reuse_detected",
                resource_type="user",
                resource_id=user_id,
                ip=ip,
                user_agent=user_agent,
                request_id=request_id,
                context={"sessionsRevoked": revoked},
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def logout(
    session: AsyncSession,
    *,
    user: User | None,
    refresh_token: str | None,
    ip: str | None,
    user_agent: str | None,
    request_id: str | None,
) -> int:
    """Logout (SECURITY §2): access+refresh -> отзыв ЕЁ; access без refresh ->
    отзыв ВСЕХ активных сессий (зафиксированное решение S1.5); режим B
    (без access, по валидной cookie) -> отзыв ЕЁ.

    Возвращает число отозванных сессий; audit logout в транзакции запроса.
    """
    revoked = 0
    mode = "all"
    subject_id: uuid.UUID | None = None

    if refresh_token is not None:
        token_hash = _sha256(refresh_token)
        status = await repository.get_refresh_status(session, token_hash)
        subject_id = status["user_id"]
        if status["status"] in ("active", "revoked"):
            # active: атомарный claim = отзыв; revoked: уже отозвана —
            # идемпотентный logout, оба случая считаем успешным выходом
            mode = "single"
            claimed = await repository.claim_refresh(session, token_hash)
            revoked = 1 if claimed is not None else 0
    elif user is not None:
        subject_id = user.id
        revoked = await repository.revoke_all_for_user(session, user.id)

    if subject_id is None and user is not None:
        subject_id = user.id

    await repository.append_audit(
        session,
        user_id=subject_id,
        action="logout",
        resource_type="user",
        resource_id=subject_id,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
        context={"mode": mode, "sessionsRevoked": revoked},
    )
    return revoked


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
