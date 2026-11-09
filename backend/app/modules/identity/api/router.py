"""Роутер identity: /api/v1/auth/{register,login,password}, /api/v1/users/me.

Refresh-ротация, logout, CSRF-guard и Redis rate limiting — S1.5
(STAGE_1_PLAN §9.1/§9.3); здесь только их контракты-заглушки (NoOp limiter).
"""

import ipaddress

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AuthError, ForbiddenError
from app.core.security.cookies import read_refresh_cookie, set_refresh_cookie
from app.modules.identity import repository, service
from app.modules.identity.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    RateLimiterDep,
)
from app.modules.identity.api.schemas import (
    AuthResponse,
    LoginRequest,
    PasswordChangeRequest,
    PasswordChangeResponse,
    RegisterRequest,
    UserMe,
    UserMePatch,
)
from app.modules.identity.models import User

router = APIRouter(prefix="/api/v1", tags=["identity"])


def _client_ip(request: Request) -> str | None:
    """IP клиента или None; не-IP (тесты/битые прокси) не должен ломать запись в INET."""
    host = request.client.host if request.client is not None else None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _auth_response(
    response: Response, user: User, tokens: service.AuthTokens, settings: Settings
) -> AuthResponse:
    # refresh — только в httpOnly-cookie (ADR-020), в JSON его нет
    set_refresh_cookie(response, tokens.refresh_token, settings)
    # model_validate вместо конструктора: pydantic-mypy ожидает alias-имена
    return AuthResponse.model_validate(
        {
            "id": str(user.id),
            "email": user.email,
            "locale": user.locale,
            "access_token": tokens.access_token,
            "expires_in": settings.access_ttl_seconds,
        }
    )


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _limiter: RateLimiterDep,
) -> AuthResponse:
    """Создание аккаунта. В personal MVP закрыто флагом -> 403 registration_disabled."""
    if not settings.allow_registration:
        raise ForbiddenError(code="registration_disabled")
    user, tokens = await service.register_user(
        session,
        settings,
        email=str(body.email),
        password=body.password,
        locale=body.locale,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
        request_id=_request_id(request),
    )
    return _auth_response(response, user, tokens, settings)


@router.post("/auth/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _limiter: RateLimiterDep,
) -> AuthResponse:
    """Вход: access в теле + refresh в cookie. Одинаково для неверного пароля
    и несуществующего email (без enumeration, SECURITY §2)."""
    try:
        user, tokens = await service.login(
            session,
            settings,
            email=str(body.email),
            password=body.password,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
            request_id=_request_id(request),
        )
    except service.InvalidCredentialsError:
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
        await service.audit_failed_login(
            factory,
            email=str(body.email),
            ip=_client_ip(request),
            user_agent=_user_agent(request),
            request_id=_request_id(request),
        )
        raise AuthError from None
    return _auth_response(response, user, tokens, settings)


@router.put("/auth/password", response_model=PasswordChangeResponse)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> PasswordChangeResponse:
    """Смена пароля (§9.2): отзыв всех сессий, кроме текущей (по refresh-cookie)."""
    access_token, _revoked = await service.change_password(
        session,
        settings,
        user,
        old_password=body.old_password,
        new_password=body.new_password,
        current_refresh_token=read_refresh_cookie(request),
        ip=_client_ip(request),
        user_agent=_user_agent(request),
        request_id=_request_id(request),
    )
    return PasswordChangeResponse.model_validate(
        {"access_token": access_token, "expires_in": settings.access_ttl_seconds}
    )


@router.get("/users/me", response_model=UserMe)
async def get_me(user: CurrentUser) -> UserMe:
    return UserMe.from_user(user)


@router.patch("/users/me", response_model=UserMe)
async def patch_me(body: UserMePatch, session: DbSession, user: CurrentUser) -> UserMe:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    await repository.update_user(session, user, **changes)
    return UserMe.from_user(user)
