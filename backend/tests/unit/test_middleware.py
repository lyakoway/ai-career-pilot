"""S1.1: middleware — X-Request-Id (эхо/генерация), security-заголовки, access-лог."""

import re
import uuid
from typing import Any

import pytest
import structlog

from app.core.config import Settings
from app.core.logging import configure_logging

UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def captured_events() -> list[dict[str, Any]]:
    """Захват событий structlog С нашими процессорами (merge_contextvars + redact).

    structlog.testing.capture_logs подменяет пайплайн и не мержит contextvars,
    поэтому для проверки requestId в access-логе используем свой capture.
    """
    events: list[dict[str, Any]] = []

    def capture(logger: Any, method: str, event_dict: dict[str, Any]) -> str:
        # Финальный процессор обязан вернуть строку (аргумент логгера),
        # само событие сохраняем в списке до возврата.
        events.append(dict(event_dict))
        return str(event_dict.get("event", ""))

    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, capture],
        cache_logger_on_first_use=False,
    )
    yield events
    configure_logging(Settings())


def test_request_id_echoed_from_client(client) -> None:
    response = client.get("/health", headers={"X-Request-Id": "trace-abc-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "trace-abc-123"


def test_request_id_generated_when_absent(client) -> None:
    response = client.get("/health")
    assert UUID_HEX_RE.fullmatch(response.headers["X-Request-Id"])
    # uuid4.hex валиден
    uuid.UUID(response.headers["X-Request-Id"])


def test_unsafe_request_id_replaced(client) -> None:
    response = client.get("/health", headers={"X-Request-Id": "bad id\nwith junk"})
    assert UUID_HEX_RE.fullmatch(response.headers["X-Request-Id"])


def test_security_headers_present(client) -> None:
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_access_log_contains_request_id_and_no_body(client, captured_events) -> None:
    response = client.get("/health", headers={"X-Request-Id": "access-log-check"})
    assert response.status_code == 200
    events = [event for event in captured_events if event.get("event") == "http_request"]
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == "access-log-check"
    assert event["method"] == "GET"
    assert event["path"] == "/health"
    assert event["status_code"] == 200
    assert "duration_ms" in event
    # Тела запросов/ответов в логах отсутствуют
    assert "body" not in event
