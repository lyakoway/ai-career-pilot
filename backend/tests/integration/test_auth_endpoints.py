"""S1.4 integration: auth-эндпоинты на реальной PostgreSQL (порт 5434, роль cp_app)."""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.db import create_db_engine, create_session_factory
from app.core.security.jwt import decode_access
from app.main import create_app
from app.modules.identity import repository
from tests.conftest import _truncate, fetch_all
from tests.fakes import FakeRedis

PASSWORD = "Str0ng-Passw0rd-Example"
JWT_SECRET = "integration-test-jwt-secret-0123456789abcdef"


def _settings(pg: dict[str, str], **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url=pg["app"],
        redis_url="redis://localhost:6379/0",
        jwt_secret=JWT_SECRET,
        ratelimit_hmac_key="integration-test-hmac-key-0123456789abcdef",
        **overrides,
    )


@pytest.fixture
def auth_client_factory(pg: dict[str, str]) -> Iterator[Any]:
    """Фабрика TestClient'ов с реальным engine на cp_app; флаги через overrides."""

    def make(**overrides: Any) -> TestClient:
        engine = create_db_engine(pg["app"])
        app: FastAPI = create_app(
            settings=_settings(pg, **overrides),
            db_engine=engine,
            redis_client=FakeRedis(),
        )
        client = TestClient(app)
        client.state_engine = engine  # type: ignore[attr-defined]
        return client

    yield make


@pytest.fixture(autouse=True)
def _clean(pg: dict[str, str]) -> Iterator[None]:
    asyncio.run(_truncate(pg["admin"]))
    yield
    asyncio.run(_truncate(pg["admin"]))


async def _seed_user(pg: dict[str, str], email: str, password: str = PASSWORD) -> str:
    from app.core.security.passwords import hash_password

    engine = create_db_engine(pg["app"])
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            user = await repository.create_user(
                session, email=email, password_hash=hash_password(password)
            )
            await session.commit()
            return str(user.id)
    finally:
        await engine.dispose()


async def _seed_refresh(pg: dict[str, str], user_id: str, token: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    engine = create_db_engine(pg["app"])
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await repository.create_refresh_session(
                session,
                user_id=uuid.UUID(user_id),
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
            await session.commit()
    finally:
        await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------- register ----------


def test_register_disabled_by_default(auth_client_factory) -> None:
    with auth_client_factory() as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": PASSWORD},
        )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "registration_disabled"


def test_register_enabled_creates_user_with_cookie_and_audit(auth_client_factory, pg) -> None:
    with auth_client_factory(allow_registration=True) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": PASSWORD, "locale": "en"},
        )
    assert response.status_code == 201
    data = response.json()
    assert set(data) == {"id", "email", "locale", "accessToken", "tokenType", "expiresIn"}
    assert data["locale"] == "en" and data["tokenType"] == "bearer" and data["expiresIn"] == 900
    decode_access(data["accessToken"], secret=JWT_SECRET)  # валидный JWT

    # cookie-атрибуты — по заголовку ответа
    set_cookie = response.headers["set-cookie"]
    assert "cp_refresh=" in set_cookie and "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie and "Path=/api/v1/auth" in set_cookie
    assert "Secure" in set_cookie  # local-инстанс с cookie_secure по умолчанию true

    users = fetch_all(pg["admin"], "SELECT email, password_hash FROM users")
    assert len(users) == 1
    assert users[0]["password_hash"].startswith("$argon2id$")
    audit = fetch_all(
        pg["admin"], "SELECT action, context FROM audit_events WHERE action='user.registered'"
    )
    assert len(audit) == 1 and audit[0]["context"] == {"via": "api"}


# ---------- login ----------


def test_login_success(auth_client_factory, pg) -> None:
    asyncio.run(_seed_user(pg, "user@example.com"))
    with auth_client_factory() as client:
        response = _login(client, "User@Example.com")  # citext — без учёта регистра
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "user@example.com"
    assert "cp_refresh=" in response.headers["set-cookie"]
    decode_access(data["accessToken"], secret=JWT_SECRET)

    audit = fetch_all(pg["admin"], "SELECT action FROM audit_events")
    assert [r["action"] for r in audit] == ["login.success"]
    assert fetch_all(pg["admin"], "SELECT 1 AS x FROM users WHERE last_login_at IS NOT NULL")


def test_login_no_enumeration_identical_responses(auth_client_factory, pg) -> None:
    asyncio.run(_seed_user(pg, "user@example.com"))
    with auth_client_factory() as client:
        wrong_password = _login(client, "user@example.com", "WrongPassword123")
        no_such_email = _login(client, "ghost@example.com", "WrongPassword123")
    assert wrong_password.status_code == no_such_email.status_code == 401
    body_a = wrong_password.json()["error"]
    body_b = no_such_email.json()["error"]
    assert body_a["code"] == body_b["code"] == "unauthorized"
    assert body_a["message"] == body_b["message"]
    assert body_a["details"] == body_b["details"] == {}

    audit = fetch_all(pg["admin"], "SELECT action, actor_subject_id FROM audit_events")
    failed = [r for r in audit if r["action"] == "login.failed"]
    assert len(failed) == 2  # audit пережил rollback 401
    assert failed[0]["actor_subject_id"] is not None  # известный email -> subject
    assert failed[1]["actor_subject_id"] is None  # несуществующий -> системное событие


# ---------- users/me ----------


def test_users_me_requires_auth(auth_client_factory) -> None:
    with auth_client_factory() as client:
        assert client.get("/api/v1/users/me").status_code == 401
        assert client.get("/api/v1/users/me", headers=_auth("garbage")).status_code == 401


def test_users_me_returns_own_profile_only(auth_client_factory, pg) -> None:
    user_a = asyncio.run(_seed_user(pg, "a@example.com"))
    asyncio.run(_seed_user(pg, "b@example.com"))
    with auth_client_factory() as client:
        response = _login(client, "a@example.com")
        token = response.json()["accessToken"]
        me = client.get("/api/v1/users/me", headers=_auth(token))
    assert me.status_code == 200
    data = me.json()
    assert data["id"] == user_a
    assert data["email"] == "a@example.com"
    assert set(data) == {
        "id",
        "email",
        "displayName",
        "locale",
        "timezone",
        "createdAt",
        "updatedAt",
    }
    assert "password" not in me.text.lower() and "hash" not in me.text.lower()


def test_users_me_patch_validation_and_update(auth_client_factory, pg) -> None:
    asyncio.run(_seed_user(pg, "a@example.com"))
    with auth_client_factory() as client:
        token = _login(client, "a@example.com").json()["accessToken"]
        bad_locale = client.patch("/api/v1/users/me", headers=_auth(token), json={"locale": "de"})
        bad_tz = client.patch(
            "/api/v1/users/me", headers=_auth(token), json={"timezone": "Mars/Olympus"}
        )
        ok = client.patch(
            "/api/v1/users/me",
            headers=_auth(token),
            json={"locale": "en", "timezone": "Europe/Berlin", "displayName": "Alex"},
        )
    assert bad_locale.status_code == 422
    assert bad_locale.json()["error"]["code"] == "validation_error"
    assert bad_tz.status_code == 422
    assert ok.status_code == 200
    data = ok.json()
    assert (data["locale"], data["timezone"], data["displayName"]) == (
        "en",
        "Europe/Berlin",
        "Alex",
    )


# ---------- password change ----------


def test_password_change_semantics(auth_client_factory, pg) -> None:
    user_id = asyncio.run(_seed_user(pg, "a@example.com"))
    asyncio.run(_seed_user(pg, "b@example.com"))
    # у A: текущая сессия rt_current + две «другие»; у B: своя
    asyncio.run(_seed_refresh(pg, user_id, "rt_current"))
    asyncio.run(_seed_refresh(pg, user_id, "rt_other_1"))
    asyncio.run(_seed_refresh(pg, user_id, "rt_other_2"))

    with auth_client_factory() as client:
        b_token = _login(client, "b@example.com").json()["accessToken"]

        token = _login(client, "a@example.com").json()["accessToken"]
        wrong_old = client.put(
            "/api/v1/auth/password",
            headers=_auth(token),
            json={"oldPassword": "WrongOld123", "newPassword": "NewStr0ng-Pass-456"},
        )
        assert wrong_old.status_code == 401

        weak_new = client.put(
            "/api/v1/auth/password",
            headers=_auth(token),
            cookies={"cp_refresh": "rt_current"},
            json={"oldPassword": PASSWORD, "newPassword": "short"},
        )
        assert weak_new.status_code == 422
        assert weak_new.json()["error"]["code"] == "validation_error"

        ok = client.put(
            "/api/v1/auth/password",
            headers=_auth(token),
            cookies={"cp_refresh": "rt_current"},
            json={"oldPassword": PASSWORD, "newPassword": "NewStr0ng-Pass-456"},
        )
    assert ok.status_code == 200
    payload = decode_access(ok.json()["accessToken"], secret=JWT_SECRET)
    assert str(payload.user_id) == user_id

    current_hash = hashlib.sha256(b"rt_current").hexdigest()
    rows = {
        r["token_hash"]: r["revoked_at"]
        for r in fetch_all(pg["admin"], "SELECT token_hash, revoked_at FROM refresh_tokens")
    }
    assert rows[current_hash] is None  # текущая — жива
    assert sum(1 for h, rv in rows.items() if h != current_hash and rv is None) == 1
    # живая non-current = сессия B (login B); отозваны: rt_other_1/2 + сессия
    # логина A в этом тесте (итого 3)
    audit = fetch_all(
        pg["admin"], "SELECT action, context FROM audit_events WHERE action='password.changed'"
    )
    assert len(audit) == 1 and audit[0]["context"] == {"sessionsRevoked": 3}

    # старый пароль больше не работает, новый — работает (tenant B не затронут)
    with auth_client_factory() as client:
        assert _login(client, "a@example.com").status_code == 401
        assert _login(client, "a@example.com", "NewStr0ng-Pass-456").status_code == 200
        assert client.get("/api/v1/users/me", headers=_auth(b_token)).status_code == 200


# ---------- логирование без секретов ----------


def test_login_path_does_not_leak_secrets_to_logs(auth_client_factory, pg) -> None:
    asyncio.run(_seed_user(pg, "log@example.com"))
    events: list[dict[str, Any]] = []

    def capture(logger: Any, method: str, event_dict: dict[str, Any]) -> str:
        events.append(dict(event_dict))
        return str(event_dict.get("event", ""))

    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, capture],
        cache_logger_on_first_use=False,
    )
    try:
        with auth_client_factory() as client:
            response = _login(client, "log@example.com")
    finally:
        from app.core.logging import configure_logging

        configure_logging(Settings(_env_file=None))
    assert response.status_code == 200
    dumped = " ".join(str(v) for event in events for v in event.values())
    assert PASSWORD not in dumped
    assert response.json()["accessToken"] not in dumped
