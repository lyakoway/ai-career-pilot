"""S1.1: PII-редакция в логах — токены/пароли/секреты по ключам, email в строках."""

import structlog

from app.core.logging import redact_processor, redact_value


def test_sensitive_keys_are_fully_redacted() -> None:
    assert redact_value("password", "hunter2") == "[REDACTED]"
    assert redact_value("refresh_token", "rt_abc") == "[REDACTED]"
    assert redact_value("Authorization", "Bearer eyJ") == "[REDACTED]"
    assert redact_value("cp_refresh", "rt_secret") == "[REDACTED]"
    assert redact_value("cookie", "cp_refresh=...") == "[REDACTED]"
    assert redact_value("api_secret", "zzz") == "[REDACTED]"


def test_emails_are_masked_in_string_values() -> None:
    assert (
        redact_value("note", "письмо на user@example.com отправлено")
        == "письмо на [EMAIL_REDACTED] отправлено"
    )


def test_non_sensitive_values_pass_through() -> None:
    assert redact_value("status", "ok") == "ok"
    assert redact_value("count", 42) == 42


def test_processor_redacts_whole_event_dict() -> None:
    event = {
        "event": "login_attempt",
        "password": "hunter2",
        "access_token": "eyJabc",
        "message": "user user@example.com failed",
    }
    redacted = redact_processor(None, "info", event)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["message"] == "user [EMAIL_REDACTED] failed"
    assert redacted["event"] == "login_attempt"


def test_configured_logger_applies_redaction(client) -> None:
    # configure_logging выполнен в create_app; проверяем, что процессор в конфигурации
    processors = structlog.get_config()["processors"]
    assert redact_processor in processors
