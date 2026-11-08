"""Конфигурация приложения: pydantic-settings, префикс CP_, fail-fast (ARCHITECTURE §9).

Контракты (STAGE_1_PLAN §4.3):
- неизвестные CP_* переменные и невалидные значения -> отказ старта
  (pydantic-settings игнорирует лишние env-переменные даже с extra="forbid",
  поэтому неизвестные CP_* проверяются явно);
- CP_JWT_SECRET и CP_RATELIMIT_HMAC_KEY: обязательны, >= 32 байт, без дефолтов;
- CP_APP_ENV=production + CP_COOKIE_SECURE=false -> отказ старта (local + false допустимо);
- production отклоняет dev-заглушки секретов ("change-me...").
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "ValidationError", "get_settings"]

_MIN_SECRET_BYTES = 32
_DEV_PLACEHOLDER_PREFIX = "change-me"


class Settings(BaseSettings):
    """Все настройки приложения; источник — environment (локально дополнительно .env)."""

    model_config = SettingsConfigDict(
        env_prefix="CP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    app_env: Literal["local", "production"] = "local"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] | None = None  # None -> по app_env

    database_url: str
    # Миграции выполняются под отдельной ролью (STAGE_1_PLAN §8.2); пусто -> database_url
    migrations_database_url: str = ""
    redis_url: str

    jwt_secret: str
    ratelimit_hmac_key: str

    access_ttl_seconds: int = Field(default=900, gt=0)
    refresh_ttl_days: int = Field(default=30, gt=0)
    allow_registration: bool = False
    allowed_origin: str = ""
    cookie_secure: bool = True

    rate_limit_auth_per_minute_ip: int = Field(default=30, gt=0)
    rate_limit_login_per_minute_ip: int = Field(default=10, gt=0)
    rate_limit_login_failures_per_account: int = Field(default=10, gt=0)
    rate_limit_login_failures_window_seconds: int = Field(default=900, gt=0)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_log_format(self) -> Literal["console", "json"]:
        if self.log_format is not None:
            return self.log_format
        return "json" if self.is_production else "console"

    @field_validator("jwt_secret", "ratelimit_hmac_key")
    @classmethod
    def _secret_min_length(cls, value: str, info: ValidationInfo) -> str:
        if len(value.encode("utf-8")) < _MIN_SECRET_BYTES:
            msg = f"{info.field_name} must be at least {_MIN_SECRET_BYTES} bytes"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _fail_fast_rules(self) -> "Settings":
        known_fields = set(type(self).model_fields)
        unknown = sorted(
            key
            for key in os.environ
            # CP_TEST_* — тестовая инфраструктура (URL тестовой БД), не конфиг приложения
            if key.startswith("CP_")
            and not key.startswith("CP_TEST_")
            and key.removeprefix("CP_").lower() not in known_fields
        )
        if unknown:
            msg = f"Unknown CP_* environment variables: {unknown}"
            raise ValueError(msg)
        if self.is_production:
            if not self.cookie_secure:
                msg = "CP_COOKIE_SECURE=false is forbidden in production"
                raise ValueError(msg)
            for field_name in ("jwt_secret", "ratelimit_hmac_key"):
                value = getattr(self, field_name)
                if value.startswith(_DEV_PLACEHOLDER_PREFIX):
                    msg = f"{field_name} must not use a dev placeholder in production"
                    raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Настройки процесса (кеш на первый вызов — валидация один раз при старте)."""
    return Settings()
