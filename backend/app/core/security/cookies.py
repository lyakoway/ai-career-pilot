"""Refresh-cookie `cp_refresh` (ADR-020, SECURITY §2).

Атрибуты: HttpOnly; Secure (CP_COOKIE_SECURE); SameSite=Lax;
Path=/api/v1/auth (куки уходят только на auth-эндпоинты);
Max-Age = CP_REFRESH_TTL_DAYS * 86400.
"""

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings

__all__ = [
    "AUTH_COOKIE_PATH",
    "REFRESH_COOKIE_NAME",
    "clear_refresh_cookie",
    "read_refresh_cookie",
    "set_refresh_cookie",
]

REFRESH_COOKIE_NAME = "cp_refresh"
AUTH_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Выдать refresh-cookie с атрибутами ADR-020."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.refresh_ttl_days * 86400,
        path=AUTH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Снять refresh-cookie (logout): те же Path/атрибуты, Max-Age=0."""
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def read_refresh_cookie(request: Request) -> str | None:
    """Значение refresh-cookie запроса или None (режимы см. STAGE_1_PLAN §9.1)."""
    return request.cookies.get(REFRESH_COOKIE_NAME)
