"""HTTP-middleware: RequestID (внешний) -> AccessLog -> SecurityHeaders (внутренний).

Порядок обеспечивается порядком add_middleware в main.py (последний добавленный —
внешний). Тела запросов/ответов не логируются (SECURITY §2).
"""

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["AccessLogMiddleware", "RequestIDMiddleware", "SecurityHeadersMiddleware"]

_REQUEST_ID_HEADER = "X-Request-Id"
# Принимаем от клиента только безопасный ограниченный формат; иначе генерируем свой.
_EXTERNAL_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_ACCESS_LOGGER = structlog.get_logger("http.access")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """X-Request-Id: эхо валидного клиентского или генерация uuid4-hex; эхо в ответе."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        structlog.contextvars.clear_contextvars()
        request_id = request.headers.get(_REQUEST_ID_HEADER, "")
        if not _EXTERNAL_REQUEST_ID_RE.fullmatch(request_id):
            request_id = uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Одна строка на запрос: метод, путь, статус, длительность; без тел."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            _ACCESS_LOGGER.error(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        _ACCESS_LOGGER.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security-заголовки для API (CSP — ответственность frontend, S1.x нет)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response
