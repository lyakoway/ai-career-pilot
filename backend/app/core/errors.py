"""Иерархия доменных ошибок и единый формат ответа (ARCHITECTURE §7.1, API_SPEC §8).

Формат: {"error": {"code", "message", "details", "requestId"}}.
Стек-трейсы наружу не отдаются — только в логи.
"""

from typing import Any

__all__ = [
    "AppError",
    "AuthError",
    "ConflictError",
    "ExternalServiceError",
    "ForbiddenError",
    "InternalError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
]


class AppError(Exception):
    """База: стабильный код + HTTP-статус; message может быть переопределён."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message
        self.code = code or self.code
        self.details = details or {}


class ValidationError(AppError):
    code = "validation_error"
    http_status = 422


class AuthError(AppError):
    code = "unauthorized"
    http_status = 401


class ForbiddenError(AppError):
    code = "forbidden"
    http_status = 403


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ConflictError(AppError):
    code = "conflict"
    http_status = 409


class RateLimitError(AppError):
    code = "rate_limited"
    http_status = 429


class ExternalServiceError(AppError):
    """Внешняя зависимость недоступна; для auth — fail-closed 503 (STAGE_1_PLAN §9.3)."""

    code = "external_service_error"
    http_status = 503


class InternalError(AppError):
    code = "internal_error"
    http_status = 500
