"""Зависимости identity: сессия, текущий пользователь, настройки, rate-limiter."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session
from app.core.errors import AuthError
from app.core.logging import bind_user_id
from app.core.ratelimit import RateLimiter
from app.core.security.jwt import TokenExpiredError, TokenInvalidError, decode_access
from app.modules.identity import repository
from app.modules.identity.models import User

__all__ = ["CurrentUser", "DbSession", "get_app_settings", "get_current_user", "get_rate_limiter"]

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_app_settings(request: Request) -> Settings:
    """Настройки инстанса приложения (переопределяются в тестах через create_app)."""
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


async def get_current_user(request: Request, session: DbSession) -> User:
    """Authorization: Bearer -> access-JWT -> активный пользователь; иначе 401."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError
    try:
        payload = decode_access(token, secret=request.app.state.settings.jwt_secret)
    except (TokenExpiredError, TokenInvalidError) as exc:
        raise AuthError from exc

    user = await repository.get_user_by_id(session, payload.user_id)
    if user is None or not user.is_active:
        raise AuthError
    bind_user_id(str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_rate_limiter(request: Request) -> RateLimiter:
    """NoOp в S1.4; Redis-реализация подключается в S1.5 без правки роутера."""
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
