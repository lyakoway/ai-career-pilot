"""S1.4 unit: camelCase-DTO и get_current_user без БД (битые токены)."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import AuthError
from app.modules.identity.api.deps import get_current_user
from app.modules.identity.api.schemas import (
    AuthResponse,
    LoginRequest,
    PasswordChangeRequest,
    UserMePatch,
)


class TestSchemasAliases:
    def test_login_request_parses_body_as_is(self) -> None:
        body = LoginRequest.model_validate({"email": "a@b.com", "password": "x"})
        assert body.email == "a@b.com"

    def test_password_change_camel_case(self) -> None:
        body = PasswordChangeRequest.model_validate({"oldPassword": "old", "newPassword": "new"})
        assert body.old_password == "old"
        assert body.new_password == "new"

    def test_auth_response_serializes_camel_case(self) -> None:
        payload = AuthResponse(
            id="u",
            email="a@b.com",
            locale="ru",
            access_token="eyJ",
            expires_in=900,
        )
        data = payload.model_dump(by_alias=True)
        assert data["accessToken"] == "eyJ"
        assert data["tokenType"] == "bearer"
        assert data["expiresIn"] == 900

    def test_patch_rejects_bad_locale_and_timezone(self) -> None:
        with pytest.raises(PydanticValidationError):
            UserMePatch.model_validate({"locale": "de"})
        with pytest.raises(PydanticValidationError):
            UserMePatch.model_validate({"timezone": "Not/AZone"})
        with pytest.raises(PydanticValidationError):
            UserMePatch.model_validate({"displayName": ""})

    def test_patch_accepts_valid_values(self) -> None:
        patch = UserMePatch.model_validate(
            {"locale": "en", "timezone": "Europe/Berlin", "displayName": " Alex "}
        )
        assert patch.display_name == "Alex"
        assert patch.timezone == "Europe/Berlin"


class TestGetCurrentUserWithoutDb:
    """decode падает до касания БД — сессия не нужна (None)."""

    @pytest.mark.parametrize(
        "header",
        ["", "Basic abc", "Bearer ", "Bearer garbage.token.value"],
    )
    def test_bad_authorization_raises_auth_error(self, header: str) -> None:
        from types import SimpleNamespace

        class _Request:
            headers = {"authorization": header} if header else {}
            app = SimpleNamespace(
                state=SimpleNamespace(settings=SimpleNamespace(jwt_secret="x" * 32))
            )

        import asyncio

        with pytest.raises(AuthError):
            asyncio.run(get_current_user(_Request(), None))  # type: ignore[arg-type]
