"""Роутер identity: /api/v1/auth/{register,login,password}, /api/v1/users/me.

Refresh-ротация, logout, CSRF-guard и Redis rate limiting — S1.5
(STAGE_1_PLAN §9.1/§9.3); здесь только их контракты-заглушки (NoOp limiter).
"""

import hashlib
import ipaddress

import structlog
from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AuthError, ForbiddenError
from app.core.security.cookies import (
    clear_refresh_cookie,
    read_refresh_cookie,
    set_refresh_cookie,
)
from app.modules.identity import repository, service
from app.modules.identity.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    RateLimiterDep,
    require_origin_guard,
    try_get_current_user,
)
from app.modules.identity.api.schemas import (
    AuthResponse,
    LoginRequest,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    UserMe,
    UserMePatch,
)
from app.modules.identity.models import User

router = APIRouter(prefix="/api/v1", tags=["identity"])

_LOGGER = structlog.get_logger("identity.router")


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
    limiter: RateLimiterDep,
) -> AuthResponse:
    """Создание аккаунта. В personal MVP закрыто флагом -> 403 registration_disabled."""
    await limiter.check_auth_ip(_client_ip(request) or "unknown")
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
    limiter: RateLimiterDep,
) -> AuthResponse:
    """Вход: access в теле + refresh в cookie. Одинаково для неверного пароля
    и несуществующего email (без enumeration, SECURITY §2)."""
    ip = _client_ip(request) or "unknown"
    await limiter.check_auth_ip(ip)
    await limiter.check_login_ip(ip)
    await limiter.check_login_failures(str(body.email))
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
        await limiter.register_login_failure(str(body.email))
        await service.audit_failed_login(
            factory,
            email=str(body.email),
            ip=_client_ip(request),
            user_agent=_user_agent(request),
            request_id=_request_id(request),
        )
        raise AuthError from None
    await limiter.clear_login_failures(str(body.email))
    return _auth_response(response, user, tokens, settings)


@router.post("/auth/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    limiter: RateLimiterDep,
) -> RefreshResponse:
    """Ротация refresh (S1.5, §9.1). Режимы: cookie (Origin-guard) | body | mixed."""
    await limiter.check_auth_ip(_client_ip(request) or "unknown")

    cookie_token = read_refresh_cookie(request)
    body_token: str | None = None
    if await _has_body(request):
        try:
            body_token = RefreshRequest.model_validate_json(await request.body()).refresh_token
        except Exception:
            body_token = None

    if cookie_token is not None:
        # cookie определяет режим (STAGE_1_PLAN §9.1): Origin-guard обязателен
        require_origin_guard(request, settings)
        refresh_token = cookie_token
        cookie_mode = True
        if body_token is not None:
            _LOGGER.warning("refresh_ambiguous_request_body_ignored")
    elif body_token is not None:
        refresh_token = body_token
        cookie_mode = False
    else:
        # ни cookie, ни тела — 401 (без режима)
        raise AuthError

    try:
        tokens = await service.rotate_refresh(
            session,
            settings,
            refresh_token=refresh_token,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
            request_id=_request_id(request),
        )
    except service.RefreshReuseDetectedError:
        await _handle_reuse_and_raise(request, session, settings, refresh_token)
    except service.InvalidCredentialsError as exc:
        raise AuthError from exc

    if cookie_mode:
        set_refresh_cookie(response, tokens.refresh_token, settings)
        return RefreshResponse.model_validate(
            {"access_token": tokens.access_token, "expires_in": settings.access_ttl_seconds}
        )
    # body-режим: новый refresh возвращается в теле (CLI не имеет cookie-jar)
    return RefreshResponse.model_validate(
        {
            "access_token": tokens.access_token,
            "expires_in": settings.access_ttl_seconds,
            "refresh_token": tokens.refresh_token,
        }
    )


async def _handle_reuse_and_raise(
    request: Request, session: AsyncSession, settings: Settings, refresh_token: str
) -> None:
    """Массовый отзыв + audit reuse отдельной транзакцией; затем 401 reuse."""
    token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    status = await repository.get_refresh_status(session, token_hash)
    user_id = status["user_id"]
    if user_id is None:
        raise AuthError from None
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    await service.handle_reuse(
        factory,
        user_id=user_id,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
        request_id=_request_id(request),
    )
    raise AuthError(code="token_reuse_detected")


async def _has_body(request: Request) -> bool:
    length = request.headers.get("content-length")
    if length is None or length == "0":
        return False
    return bool(await request.body())


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    limiter: RateLimiterDep,
) -> LogoutResponse:
    """Logout (SECURITY §2). A: access (+refresh -> её; без refresh -> все).
    B: без access, валидная refresh-cookie + Origin -> её. Cookie очищается."""
    await limiter.check_auth_ip(_client_ip(request) or "unknown")

    cookie_token = read_refresh_cookie(request)
    body_token: str | None = None
    if cookie_token is None and await _has_body(request):
        try:
            body_token = RefreshRequest.model_validate_json(await request.body()).refresh_token
        except Exception:
            body_token = None

    refresh_token = cookie_token or body_token
    user = await try_get_current_user(request, session)

    if refresh_token is None and user is None:
        raise AuthError
    if cookie_token is not None:
        # cookie-режим (даже при валидном access): Origin-guard обязателен
        require_origin_guard(request, settings)

    await service.logout(
        session,
        user=user,
        refresh_token=refresh_token,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
        request_id=_request_id(request),
    )
    if cookie_token is not None:
        clear_refresh_cookie(response, settings)
    return LogoutResponse()


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
