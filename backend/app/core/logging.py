"""Структурное логирование: structlog, JSON в prod / pretty в dev, PII-редакция.

Правила (ARCHITECTURE §8, SECURITY §2): тела запросов/ответов не логируются никогда;
значения по чувствительным ключам и email в строках маскируются процессором redact.
"""

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.config import Settings

__all__ = [
    "bind_user_id",
    "configure_logging",
    "redact_processor",
    "redact_value",
]

# Ключи, чьи значения никогда не попадают в логи целиком (SECURITY §2).
_SENSITIVE_KEY_RE = re.compile(r"password|token|refresh|authorization|cookie|secret", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_REDACTED = "[REDACTED]"
_REDACTED_EMAIL = "[EMAIL_REDACTED]"


def redact_value(key: str, value: Any) -> Any:
    """Маскировать значение по ключу: чувствительные ключи целиком, email — в строках."""
    if _SENSITIVE_KEY_RE.search(key):
        return _REDACTED
    if isinstance(value, str):
        return _EMAIL_RE.sub(_REDACTED_EMAIL, value)
    return value


def redact_processor(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog-процессор: применяется к каждому событию перед рендерингом."""
    return {key: redact_value(key, value) for key, value in event_dict.items()}


def bind_user_id(user_id: str) -> None:
    """Привязать userId к контексту логов (вызывается auth-слоем, S1.3+)."""
    bind_contextvars(user_id=user_id)


def configure_logging(settings: Settings) -> None:
    """Инициализация structlog по настройкам (формат — из resolved_log_format)."""
    renderer: structlog.typing.Processor
    if settings.resolved_log_format == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    level_name = settings.log_level.upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # False: конфигурация может переопределяться в тестах (structlog.testing.capture_logs)
        cache_logger_on_first_use=False,
    )
    clear_contextvars()
