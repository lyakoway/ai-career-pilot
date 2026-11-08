"""S1.1: fail-fast контракты конфигурации (STAGE_1_PLAN §4.3, S1.1)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_ENV = {
    "CP_APP_ENV": "local",
    "CP_DATABASE_URL": "postgresql+asyncpg://cp_app:cp_app@localhost:5432/careerpilot",
    "CP_REDIS_URL": "redis://localhost:6379/0",
    "CP_JWT_SECRET": "x" * 48,
    "CP_RATELIMIT_HMAC_KEY": "y" * 48,
}


def apply_env(
    monkeypatch: pytest.MonkeyPatch,
    drop: tuple[str, ...] = (),
    **overrides: str,
) -> None:
    """Установить чистое окружение: базовый набор + overrides, минус drop."""
    for key in [*VALID_ENV, "CP_MYSTERY_VAR"]:
        monkeypatch.delenv(key, raising=False)
    merged = {**VALID_ENV, **overrides}
    for key in drop:
        merged.pop(key, None)
    for key, value in merged.items():
        monkeypatch.setenv(key, value)


def make_settings(
    monkeypatch: pytest.MonkeyPatch,
    drop: tuple[str, ...] = (),
    **overrides: str,
) -> Settings:
    """Settings с чистым окружением (apply_env + конструирование)."""
    apply_env(monkeypatch, drop=drop, **overrides)
    return Settings()


def test_defaults_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch)
    assert settings.access_ttl_seconds == 900
    assert settings.refresh_ttl_days == 30
    assert settings.allow_registration is False
    assert settings.cookie_secure is True
    assert settings.resolved_log_format == "console"


def test_log_format_json_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, CP_APP_ENV="production")
    assert settings.is_production
    assert settings.resolved_log_format == "json"


def test_unknown_cp_variable_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_env(monkeypatch)
    monkeypatch.setenv("CP_MYSTERY_VAR", "1")
    with pytest.raises(ValidationError, match="CP_MYSTERY_VAR"):
        Settings()


def test_missing_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, drop=("CP_JWT_SECRET",))


def test_short_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, CP_JWT_SECRET="short")
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, CP_RATELIMIT_HMAC_KEY="z" * 31)


def test_production_with_insecure_cookie_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        make_settings(monkeypatch, CP_APP_ENV="production", CP_COOKIE_SECURE="false")


def test_local_with_insecure_cookie_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, CP_COOKIE_SECURE="false")
    assert settings.cookie_secure is False


def test_production_rejects_dev_placeholder_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make_settings(
            monkeypatch,
            CP_APP_ENV="production",
            CP_JWT_SECRET="change-me-some-long-placeholder-secret-value-1234",
        )


def test_invalid_env_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, CP_APP_ENV="staging")
