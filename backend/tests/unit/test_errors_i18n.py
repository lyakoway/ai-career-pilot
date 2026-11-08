"""S1.1: единый формат ошибок, локализация ru/en, отсутствие стек-трейсов наружу."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ForbiddenError, RateLimitError
from app.main import _register_exception_handlers


@pytest.fixture
def error_app() -> FastAPI:
    """Мини-приложение с нашими обработчиками и эндпоинтами, кидающими ошибки."""
    app = FastAPI()
    _register_exception_handlers(app)

    @app.get("/raise-app-error")
    def raise_app_error() -> dict[str, str]:
        raise RateLimitError(details={"retryAfterSeconds": 60})

    @app.get("/raise-with-message")
    def raise_with_message() -> dict[str, str]:
        raise ForbiddenError("Специфическое сообщение", details={"x": 1})

    @app.get("/validated")
    def validated(q: int) -> dict[str, int]:
        return {"q": q}

    return app


def test_app_error_format_and_status(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/raise-app-error")
    assert response.status_code == 429
    body = response.json()["error"]
    assert set(body) == {"code", "message", "details", "requestId"}
    assert body["code"] == "rate_limited"
    assert body["details"] == {"retryAfterSeconds": 60}


def test_localization_ru_and_en(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        ru = client.get("/raise-app-error", headers={"Accept-Language": "ru"})
        en = client.get("/raise-app-error", headers={"Accept-Language": "en-US"})
    assert ru.json()["error"]["message"] == "Слишком много запросов"
    assert en.json()["error"]["message"] == "Too many requests"


def test_default_language_is_ru(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/raise-app-error")
    assert response.json()["error"]["message"] == "Слишком много запросов"


def test_explicit_message_overrides_catalog(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/raise-with-message")
    assert response.json()["error"]["message"] == "Специфическое сообщение"
    assert response.status_code == 403


def test_request_validation_error_uses_common_format(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/validated?q=not-a-number")
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"]["errors"][0]["loc"] == ["query", "q"]


def test_unknown_route_maps_to_not_found(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/no-such-route")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    assert body["requestId"] == ""


def test_no_stack_trace_in_response(error_app: FastAPI) -> None:
    with TestClient(error_app) as client:
        response = client.get("/raise-app-error")
    text = response.text
    assert "Traceback" not in text
    assert "raise " not in text
