"""Фабрика FastAPI-приложения CareerPilot AI.

Слои (core, modules/*) подключаются по мере подэтапов; после S1.1 приложение
включает: конфигурацию (fail-fast), структурное логирование, middleware
(RequestID -> AccessLog -> SecurityHeaders), единую обработку ошибок (ru/en),
/health (liveness) и /ready (readiness: PostgreSQL + Redis).
Зависимости (engine/redis) можно инжектировать — тесты передают фейки.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis_asyncio
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import Settings, get_settings
from app.core.db import create_db_engine
from app.core.errors import AppError
from app.core.i18n import parse_accept_language, translate
from app.core.logging import configure_logging
from app.core.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.readiness import RedisGateway, check_readiness

OPENAPI_URL = "/api/openapi.json"

_logger = structlog.get_logger("app")

# Маппинг стандартных HTTP-статусов на стабильные коды ошибок (API_SPEC §8).
_HTTP_STATUS_TO_CODE = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


def _error_body(
    request: Request, code: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    """Единый формат ошибки; requestId приходит из RequestID-middleware."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "requestId": getattr(request.state, "request_id", ""),
        }
    }


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        lang = parse_accept_language(request.headers.get("accept-language"))
        message = exc.message or translate(exc.code, lang)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(request, exc.code, message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        lang = parse_accept_language(request.headers.get("accept-language"))
        details = {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "validation_error", translate("validation_error", lang), details
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        lang = parse_accept_language(request.headers.get("accept-language"))
        code = _HTTP_STATUS_TO_CODE.get(exc.status_code, "internal_error")
        message = exc.detail if isinstance(exc.detail, str) else translate(code, lang)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, code, message, {}),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Наружу — обезличенно; полный трейс — только в лог (SECURITY §6).
        _logger.error("unhandled_exception", error=str(exc), exc_info=True)
        lang = parse_accept_language(request.headers.get("accept-language"))
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "internal_error", translate("internal_error", lang), {}),
        )


def create_app(
    settings: Settings | None = None,
    db_engine: Any | None = None,
    redis_client: RedisGateway | None = None,
) -> FastAPI:
    """Собрать приложение; зависимости инжектируемы для тестов."""
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Неинжектированные зависимости создаём и закрываем сами (owned_*);
        # инжектированные (тестовые фейки) принадлежат вызывающему — не трогаем.
        engine: Any = db_engine
        client: RedisGateway
        owned_engine = None
        owned_redis = None
        if engine is None:
            owned_engine = create_db_engine(app_settings.database_url)
            engine = owned_engine
        if redis_client is None:
            owned_redis = redis_asyncio.from_url(app_settings.redis_url)  # type: ignore[no-untyped-call]
            client = owned_redis
        else:
            client = redis_client
        app.state.db_engine = engine
        app.state.redis_client = client
        try:
            yield
        finally:
            if owned_redis is not None:
                await owned_redis.aclose()
            if owned_engine is not None:
                await owned_engine.dispose()

    app = FastAPI(
        title="CareerPilot AI API",
        version="0.1.0",
        openapi_url=OPENAPI_URL,
        docs_url="/api/docs",
        lifespan=lifespan,
    )

    # Порядок add_middleware: последний добавленный — внешний.
    # Итоговый порядок (внешний -> внутренний): RequestID -> AccessLog -> SecurityHeaders.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    _register_exception_handlers(app)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness-проба: процесс жив; зависимости не проверяются."""
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        """Readiness: SELECT 1 к PostgreSQL + PING к Redis; 503 при сбое любой."""
        report = await check_readiness(app.state.db_engine, app.state.redis_client)
        return JSONResponse(
            status_code=200 if report.ok else 503,
            content={
                "status": "ok" if report.ok else "unavailable",
                "checks": report.checks,
            },
        )

    return app


app = create_app()
