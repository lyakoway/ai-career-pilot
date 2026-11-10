"""Pydantic-DTO identity: camelCase-alias в JSON, snake_case внутри (API_SPEC §1)."""

from datetime import datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

if TYPE_CHECKING:
    from app.modules.identity.models import User

_DISPLAY_NAME_MAX = 100


def _base_config() -> ConfigDict:
    return ConfigDict(populate_by_name=True)


class RegisterRequest(BaseModel):
    model_config = _base_config()

    email: EmailStr = Field(alias="email")
    password: str = Field(alias="password")
    locale: Literal["ru", "en"] = Field(alias="locale", default="ru")


class LoginRequest(BaseModel):
    model_config = _base_config()

    email: EmailStr = Field(alias="email")
    password: str = Field(alias="password")


class AuthResponse(BaseModel):
    """Тело ответа login/register: access в JSON, refresh — только в cookie."""

    model_config = _base_config()

    id: str = Field(alias="id")
    email: EmailStr = Field(alias="email")
    locale: str = Field(alias="locale")
    access_token: str = Field(alias="accessToken")
    token_type: Literal["bearer"] = Field(alias="tokenType", default="bearer")
    expires_in: int = Field(alias="expiresIn")


class PasswordChangeRequest(BaseModel):
    model_config = _base_config()

    old_password: str = Field(alias="oldPassword")
    new_password: str = Field(alias="newPassword")


class PasswordChangeResponse(BaseModel):
    model_config = _base_config()

    access_token: str = Field(alias="accessToken")
    expires_in: int = Field(alias="expiresIn")


class RefreshRequest(BaseModel):
    """Body-режим: refresh в теле (cookie отсутствует); CLI/тесты."""

    model_config = _base_config()

    refresh_token: str = Field(alias="refreshToken")


class RefreshResponse(BaseModel):
    """Cookie-режим: refresh только в Set-Cookie; body-режим: refreshToken в теле."""

    model_config = _base_config()

    access_token: str = Field(alias="accessToken")
    token_type: Literal["bearer"] = Field(alias="tokenType", default="bearer")
    expires_in: int = Field(alias="expiresIn")
    refresh_token: str | None = Field(
        alias="refreshToken", default=None
    )  # заполняется только в body-режиме


class LogoutResponse(BaseModel):
    model_config = _base_config()

    status: Literal["ok"] = Field(alias="status", default="ok")


class UserMe(BaseModel):
    """Профиль пользователя без секретов (password_hash не существует в DTO)."""

    model_config = _base_config()

    id: str = Field(alias="id")
    email: EmailStr = Field(alias="email")
    display_name: str | None = Field(alias="displayName")
    locale: str = Field(alias="locale")
    timezone: str = Field(alias="timezone")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @classmethod
    def from_user(cls, user: "User") -> "UserMe":
        return cls.model_validate(
            {
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "locale": user.locale,
                "timezone": user.timezone,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
            }
        )


class UserMePatch(BaseModel):
    model_config = _base_config()

    locale: Literal["ru", "en"] | None = Field(alias="locale", default=None)
    timezone: str | None = Field(alias="timezone", default=None)
    display_name: str | None = Field(alias="displayName", default=None)

    @field_validator("timezone")
    @classmethod
    def _valid_iana_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = "timezone must be a valid IANA zone"
            raise ValueError(msg) from exc
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name_length(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not 1 <= len(value.strip()) <= _DISPLAY_NAME_MAX:
            msg = f"displayName length must be 1..{_DISPLAY_NAME_MAX}"
            raise ValueError(msg) from None
        return value.strip()
