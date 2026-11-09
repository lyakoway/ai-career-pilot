"""S1.3: refresh-cookie cp_refresh — атрибуты ADR-020/SECURITY §2."""

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings
from app.core.security.cookies import (
    AUTH_COOKIE_PATH,
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    read_refresh_cookie,
    set_refresh_cookie,
)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _cookie_headers(response: Response) -> str:
    return "; ".join(response.headers.getlist("set-cookie"))


def test_set_cookie_attributes_when_secure() -> None:
    response = Response()
    set_refresh_cookie(response, "rt_test_token", _settings(refresh_ttl_days=30))
    cookie = _cookie_headers(response)
    assert f"{REFRESH_COOKIE_NAME}=rt_test_token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert f"Path={AUTH_COOKIE_PATH}" in cookie
    assert "Max-Age=2592000" in cookie


def test_set_cookie_without_secure_when_local() -> None:
    response = Response()
    set_refresh_cookie(response, "rt_test_token", _settings(cookie_secure=False))
    cookie = _cookie_headers(response)
    assert "Secure" not in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_clear_cookie_same_path() -> None:
    response = Response()
    clear_refresh_cookie(response, _settings())
    cookie = _cookie_headers(response)
    assert REFRESH_COOKIE_NAME in cookie
    assert f"Path={AUTH_COOKIE_PATH}" in cookie
    assert 'Max-Age="0"' in cookie or "Max-Age=0" in cookie


def test_read_cookie_present() -> None:
    request = Request({"type": "http", "method": "GET", "headers": [], "query_string": ""})
    request._cookies = {REFRESH_COOKIE_NAME: "rt_value"}
    assert read_refresh_cookie(request) == "rt_value"


def test_read_cookie_absent_returns_none() -> None:
    request = Request({"type": "http", "method": "GET", "headers": [], "query_string": ""})
    request._cookies = {}
    assert read_refresh_cookie(request) is None
