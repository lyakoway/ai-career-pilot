"""Локализация сообщений об ошибках (ru/en) по стабильным кодам (API_SPEC §8)."""

from collections.abc import Mapping
from typing import Literal

Lang = Literal["ru", "en"]

DEFAULT_LANG: Lang = "ru"

MESSAGES: Mapping[str, Mapping[Lang, str]] = {
    "validation_error": {
        "ru": "Запрос не прошёл валидацию",
        "en": "Request validation failed",
    },
    "unauthorized": {
        "ru": "Требуется аутентификация",
        "en": "Authentication required",
    },
    "token_reuse_detected": {
        "ru": "Обнаружено повторное использование refresh-токена; все сессии отозваны",
        "en": "Refresh token reuse detected; all sessions revoked",
    },
    "registration_disabled": {
        "ru": "Регистрация закрыта",
        "en": "Registration is disabled",
    },
    "forbidden": {
        "ru": "Доступ запрещён",
        "en": "Access denied",
    },
    "not_found": {
        "ru": "Ресурс не найден",
        "en": "Resource not found",
    },
    "conflict": {
        "ru": "Конфликт состояния",
        "en": "State conflict",
    },
    "domain_rule_violation": {
        "ru": "Нарушено бизнес-правило",
        "en": "Business rule violation",
    },
    "rate_limited": {
        "ru": "Слишком много запросов",
        "en": "Too many requests",
    },
    "external_service_error": {
        "ru": "Внешний сервис временно недоступен",
        "en": "External service is temporarily unavailable",
    },
    "internal_error": {
        "ru": "Внутренняя ошибка сервера",
        "en": "Internal server error",
    },
}


def parse_accept_language(header: str | None) -> Lang:
    """Определить язык по Accept-Language; дефолт и fallback — ru (API_SPEC §1)."""
    if header and header.strip().lower().startswith("en"):
        return "en"
    return DEFAULT_LANG


def translate(code: str, lang: Lang) -> str:
    """Локализованное сообщение по коду ошибки; fallback ru, затем generic-текст."""
    entry = MESSAGES.get(code)
    if entry is None:
        return MESSAGES["internal_error"][DEFAULT_LANG]
    return entry.get(lang) or entry[DEFAULT_LANG]
