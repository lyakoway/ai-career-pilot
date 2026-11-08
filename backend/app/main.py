"""Фабрика FastAPI-приложения CareerPilot AI.

Слои (core, modules/*) подключаются по мере подэтапов S1.1+;
в S1.0 приложение предоставляет только liveness-пробу и OpenAPI.
"""

from fastapi import FastAPI

OPENAPI_URL = "/api/openapi.json"


def create_app() -> FastAPI:
    """Собрать приложение: middleware, роутеры и обработчики добавляются в S1.1+."""
    app = FastAPI(
        title="CareerPilot AI API",
        version="0.1.0",
        openapi_url=OPENAPI_URL,
        docs_url="/api/docs",
    )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness-проба: процесс жив; зависимости не проверяются (это /ready, S1.1)."""
        return {"status": "ok"}

    return app


app = create_app()
