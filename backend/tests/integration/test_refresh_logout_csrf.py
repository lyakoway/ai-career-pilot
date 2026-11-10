"""S1.5 integration: ротация refresh (AC ①–⑧ на РЕАЛЬНОЙ PG), CSRF-режимы, logout A/B.

AC ⑤/⑥/⑦/⑧ — на уровне сервисных транзакций (независимые PG-сессии,
конкурентные claim'ы, без HTTP-моков); AC ①② — через HTTP с ASGITransport
(реальные транзакции приложения параллельно). AC ③④ и режимы — TestClient.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.db import create_db_engine, create_session_factory
from app.core.ratelimit import NoOpRateLimiter
from app.core.security.jwt import encode_access
from app.main import create_app
from app.modules.identity import repository, service
from tests.conftest import _truncate, fetch_all
from tests.fakes import FakeRedis

PASSWORD = "Str0ng-Passw0rd-Example"
JWT_SECRET = "integration-test-jwt-secret-0123456789abcdef"
ORIGIN = "https://app.example.com"


def _settings(pg: dict[str, str], **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url=pg["app"],
        redis_url="redis://localhost:6379/0",
        jwt_secret=JWT_SECRET,
        ratelimit_hmac_key="integration-test-hmac-key-0123456789abcdef",
        allowed_origin=ORIGIN,
        **overrides,
    )


@pytest.fixture
def engine_factory(pg: dict[str, str]) -> Iterator[Any]:
    engine = create_db_engine(pg["app"])
    yield create_session_factory(engine)
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean(pg: dict[str, str]) -> Iterator[None]:
    asyncio.run(_truncate(pg["admin"]))
    yield
    asyncio.run(_truncate(pg["admin"]))


async def _seed_user(pg: dict[str, str], email: str) -> str:
    from app.core.security.passwords import hash_password

    engine = create_db_engine(pg["app"])
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            user = await repository.create_user(
                session, email=email, password_hash=hash_password(PASSWORD)
            )
            await session.commit()
            return str(user.id)
    finally:
        await engine.dispose()


async def _insert_token(pg: dict[str, str], user_id: str, token: str, **opts: Any) -> None:
    await _exec(
        pg,
        "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked_at) "
        "VALUES (gen_random_uuid(), :u, :h, :exp, :rev)",
        {
            "u": user_id,
            "h": hashlib.sha256(token.encode()).hexdigest(),
            "exp": opts.get("expires_at") or datetime.now(UTC) + timedelta(days=30),
            "rev": opts.get("revoked_at"),
        },
    )


async def _exec(pg: dict[str, str], sql: str, params: dict[str, Any]) -> None:
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


def _client(pg: dict[str, str], **overrides: Any) -> TestClient:
    return TestClient(
        create_app(
            settings=_settings(pg, **overrides),
            db_engine=create_db_engine(pg["app"]),
            redis_client=FakeRedis(),
            rate_limiter=NoOpRateLimiter(),
        )
    )


def _http_app(pg: dict[str, str]) -> FastAPI:
    return create_app(
        settings=_settings(pg),
        db_engine=create_db_engine(pg["app"]),
        redis_client=FakeRedis(),
        rate_limiter=NoOpRateLimiter(),
    )


def _active_tokens(pg: dict[str, str]) -> int:
    return len(
        fetch_all(
            pg["admin"],
            "SELECT 1 AS x FROM refresh_tokens WHERE revoked_at IS NULL AND expires_at > now()",
        )
    )


async def _active_tokens_async(pg: dict[str, str]) -> int:
    """Для async-тестов: fetch_all использует asyncio.run — нельзя внутри loop."""
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM refresh_tokens "
                    "WHERE revoked_at IS NULL AND expires_at > now()"
                )
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


async def _audit_count_async(pg: dict[str, str], action: str | None = None) -> int:
    """Число audit-событий (всех или конкретного action) — для async-тестов."""
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.connect() as conn:
            if action is None:
                result = await conn.execute(text("SELECT count(*) FROM audit_events"))
            else:
                result = await conn.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


# ============ AC ⑤⑥⑦⑧: SQL-уровень, реальные конкурентные PG-транзакции ============


async def test_ac5_concurrent_sql_claims(pg: dict[str, str], engine_factory) -> None:
    """⑤ Две транзакции claim: победитель ротирует, проигравший видит reuse."""
    user_id = await _seed_user(pg, "sql@example.com")
    token = "rt_sql_race"
    await _insert_token(pg, user_id, token)
    settings = _settings(pg)

    async def winner(factory) -> str:
        async with factory() as session:
            tokens = await service.rotate_refresh(
                session, settings, refresh_token=token, ip=None, user_agent=None, request_id="t"
            )
            await session.commit()
            return tokens.refresh_token

    async def loser(factory) -> int:
        async with factory() as session:
            try:
                await service.rotate_refresh(
                    session,
                    settings,
                    refresh_token=token,
                    ip=None,
                    user_agent=None,
                    request_id="t",
                )
            except service.RefreshReuseDetectedError:
                return 1
            return 0

    new_token = await winner(engine_factory)
    assert await loser(engine_factory) == 1
    assert new_token != token
    assert await _active_tokens_async(pg) == 1  # инвариант ≤1 (AC ②)


async def test_ac6_ac8_reuse_mass_revoke_atomic_with_audit(
    pg: dict[str, str], engine_factory
) -> None:
    """⑥⑧ Массовый отзыв + audit — ОДНА транзакция; чужие сессии живы."""
    user_id = await _seed_user(pg, "reuse@example.com")
    victim = await _seed_user(pg, "victim@example.com")
    now = datetime.now(UTC)
    await _insert_token(pg, user_id, "rt_reused", revoked_at=now)
    await _insert_token(pg, user_id, "rt_active_1")
    await _insert_token(pg, user_id, "rt_active_2")
    await _insert_token(pg, victim, "rt_victim_active")

    await service.handle_reuse(
        engine_factory, user_id=uuid.UUID(user_id), ip=None, user_agent=None, request_id="t"
    )
    victim_hash = hashlib.sha256(b"rt_victim_active").hexdigest()
    engine = create_db_engine(pg["admin"])
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(text("SELECT token_hash, revoked_at FROM refresh_tokens"))
            ).all()
            audit_rows = (
                await conn.execute(
                    text("SELECT context FROM audit_events WHERE action = 'refresh.reuse_detected'")
                )
            ).all()
    finally:
        await engine.dispose()
    by_hash = {r[0]: r[1] for r in rows}
    assert all(rv is not None for h, rv in by_hash.items() if h != victim_hash)
    assert by_hash[victim_hash] is None
    assert len(audit_rows) == 1 and audit_rows[0][0]["sessionsRevoked"] == 2


async def test_ac7_rollback_invariant(pg: dict[str, str], engine_factory, monkeypatch) -> None:
    """⑦ Rollback-инвариант: сбой между revoke и audit -> НИЧЕГО не осталось."""
    user_id = await _seed_user(pg, "rollback@example.com")
    await _insert_token(pg, user_id, "rt_rb_active_1")
    await _insert_token(pg, user_id, "rt_rb_active_2")

    before = await _active_tokens_async(pg)
    audit_before = await _audit_count_async(pg)

    async def exploding_append(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(repository, "append_audit", exploding_append)
    with pytest.raises(RuntimeError):
        await service.handle_reuse(
            engine_factory, user_id=uuid.UUID(user_id), ip=None, user_agent=None, request_id="t"
        )
    monkeypatch.undo()

    assert await _active_tokens_async(pg) == before
    assert await _audit_count_async(pg) == audit_before


# ============ AC ①②: HTTP с реальными транзакциями (ASGITransport) ============


async def test_ac1_concurrent_http_refresh(pg: dict[str, str]) -> None:
    """① Два конкурентных HTTP refresh одним токеном: {200, 401 reuse}, сессий 0."""
    user_id = await _seed_user(pg, "race@example.com")
    await _insert_token(pg, user_id, "rt_http_race")
    app = _http_app(pg)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
            responses = await asyncio.gather(
                client.post(
                    "/api/v1/auth/refresh",
                    cookies={"cp_refresh": "rt_http_race"},
                    headers={"Origin": ORIGIN},
                ),
                client.post(
                    "/api/v1/auth/refresh",
                    cookies={"cp_refresh": "rt_http_race"},
                    headers={"Origin": ORIGIN},
                ),
            )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 401]
    loser = next(r for r in responses if r.status_code == 401)
    assert loser.json()["error"]["code"] == "token_reuse_detected"
    # fail-safe AC ⑦: после reuse все сессии отозваны (включая новый токен победителя)
    assert await _active_tokens_async(pg) == 0
    assert await _audit_count_async(pg, "refresh.reuse_detected") == 1


async def test_ac2_calm_cycle_invariant(pg: dict[str, str]) -> None:
    """② Спокойный цикл: 3 ротации подряд — всегда ровно 1 активный токен."""
    user_id = await _seed_user(pg, "calm@example.com")
    await _insert_token(pg, user_id, "rt_calm_0")
    app = _http_app(pg)
    token = "rt_calm_0"
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
            for _ in range(3):
                response = await client.post(
                    "/api/v1/auth/refresh",
                    cookies={"cp_refresh": token},
                    headers={"Origin": ORIGIN},
                )
                assert response.status_code == 200, response.text
                assert await _active_tokens_async(pg) == 1
                set_cookie = response.headers["set-cookie"]
                token = set_cookie.split("cp_refresh=")[1].split(";")[0]
    assert await _active_tokens_async(pg) == 1


async def test_ac3_reuse_after_rotation_mass_revoke(pg: dict[str, str]) -> None:
    """③ Повторное использование уже ротированного токена -> массовый отзыв."""
    user_id = await _seed_user(pg, "reuse3@example.com")
    await _insert_token(pg, user_id, "rt_reuse3_old")
    app = _http_app(pg)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
            first = await client.post(
                "/api/v1/auth/refresh",
                cookies={"cp_refresh": "rt_reuse3_old"},
                headers={"Origin": ORIGIN},
            )
            assert first.status_code == 200
            assert await _active_tokens_async(pg) == 1
            replay = await client.post(
                "/api/v1/auth/refresh",
                cookies={"cp_refresh": "rt_reuse3_old"},
                headers={"Origin": ORIGIN},
            )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reuse_detected"
    assert await _active_tokens_async(pg) == 0


# ============ Классификация (sync-тесты с готовым сидом) ============


def test_expired_is_not_reuse(pg: dict[str, str]) -> None:
    """expired ≠ reuse: 401 unauthorized БЕЗ массового отзыва."""
    user_id = asyncio.run(_seed_user(pg, "expired@example.com"))
    other = asyncio.run(_seed_user(pg, "other@example.com"))
    asyncio.run(
        _insert_token(
            pg, user_id, "rt_expired", expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )
    )
    asyncio.run(_insert_token(pg, other, "rt_other_alive"))
    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            cookies={"cp_refresh": "rt_expired"},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert _active_tokens(pg) == 1
    assert not fetch_all(
        pg["admin"], "SELECT 1 AS x FROM audit_events WHERE action='refresh.reuse_detected'"
    )


def test_unknown_token_is_unauthorized(pg: dict[str, str]) -> None:
    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            cookies={"cp_refresh": "rt_nope"},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# ============ CSRF-режимы (§9.1) ============


def test_csrf_cookie_mode_requires_matching_origin(pg: dict[str, str]) -> None:
    user_id = asyncio.run(_seed_user(pg, "csrf@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_csrf"))
    with _client(pg) as client:
        no_headers = client.post("/api/v1/auth/refresh", cookies={"cp_refresh": "rt_csrf"})
        bad = client.post(
            "/api/v1/auth/refresh",
            cookies={"cp_refresh": "rt_csrf"},
            headers={"Origin": "https://evil.example"},
        )
        ok = client.post(
            "/api/v1/auth/refresh",
            cookies={"cp_refresh": "rt_csrf"},
            headers={"Referer": f"{ORIGIN}/dashboard"},
        )
    assert no_headers.status_code == 403
    assert bad.status_code == 403
    assert ok.status_code == 200


def test_csrf_body_mode_without_origin_is_ok(pg: dict[str, str]) -> None:
    """Body-режим: без Origin; новый refresh возвращается в теле (CLI)."""
    user_id = asyncio.run(_seed_user(pg, "body@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_body_mode"))
    with _client(pg) as client:
        response = client.post("/api/v1/auth/refresh", json={"refreshToken": "rt_body_mode"})
    assert response.status_code == 200
    data = response.json()
    assert data["accessToken"]
    assert data["refreshToken"].startswith("rt_")
    assert _active_tokens(pg) == 1


def test_mixed_cookie_and_body_cookie_wins(pg: dict[str, str]) -> None:
    """Неоднозначный запрос: cookie имеет приоритет; тело игнорируется."""
    user_id = asyncio.run(_seed_user(pg, "mixed@example.com"))
    other = asyncio.run(_seed_user(pg, "mixed-other@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_cookie_real"))
    asyncio.run(_insert_token(pg, other, "rt_body_foreign"))

    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            cookies={"cp_refresh": "rt_cookie_real"},
            headers={"Origin": ORIGIN},
            json={"refreshToken": "rt_body_foreign"},
        )
    assert response.status_code == 200
    foreign = fetch_all(
        pg["admin"],
        "SELECT rt.revoked_at FROM refresh_tokens rt "
        "JOIN users u ON u.id = rt.user_id WHERE u.email = 'mixed-other@example.com'",
    )
    assert foreign[0]["revoked_at"] is None
    # 2 активных: новый ротированный cookie-токен mixed@ + нетронутый чужой body-токен
    assert _active_tokens(pg) == 2


# ============ Logout A/B (AC ④) ============


def test_logout_mode_a_with_refresh_revokes_only_it(pg: dict[str, str]) -> None:
    user_id = asyncio.run(_seed_user(pg, "loa@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_loa_current"))
    asyncio.run(_insert_token(pg, user_id, "rt_loa_other"))
    access = encode_access(uuid.UUID(user_id), "ru", 900, secret=JWT_SECRET)
    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access}", "Origin": ORIGIN},
            cookies={"cp_refresh": "rt_loa_current"},
        )
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert _active_tokens(pg) == 1
    audit = fetch_all(pg["admin"], "SELECT context FROM audit_events WHERE action='logout'")
    assert audit[0]["context"]["mode"] == "single"


def test_logout_mode_a_without_refresh_revokes_all(pg: dict[str, str]) -> None:
    user_id = asyncio.run(_seed_user(pg, "loaa@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_loaa_1"))
    asyncio.run(_insert_token(pg, user_id, "rt_loaa_2"))
    access = encode_access(uuid.UUID(user_id), "ru", 900, secret=JWT_SECRET)
    with _client(pg) as client:
        response = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 200
    assert _active_tokens(pg) == 0
    audit = fetch_all(pg["admin"], "SELECT context FROM audit_events WHERE action='logout'")
    assert audit[0]["context"]["mode"] == "all"


def test_ac4_logout_mode_b_expired_access_via_cookie(pg: dict[str, str]) -> None:
    """④ Logout при истёкшем access по refresh-cookie (+Origin) отзывает сессию."""
    user_id = asyncio.run(_seed_user(pg, "lob@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_lob_current"))
    expired_access = encode_access(uuid.UUID(user_id), "ru", -10, secret=JWT_SECRET)
    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {expired_access}", "Origin": ORIGIN},
            cookies={"cp_refresh": "rt_lob_current"},
        )
        set_cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 200
    assert "cp_refresh=" in set_cookie
    assert "Max-Age=0" in set_cookie or 'Max-Age="0"' in set_cookie
    assert _active_tokens(pg) == 0


def test_logout_without_anything_is_401(pg: dict[str, str]) -> None:
    with _client(pg) as client:
        assert client.post("/api/v1/auth/logout").status_code == 401


def test_logout_cookie_without_origin_is_403(pg: dict[str, str]) -> None:
    user_id = asyncio.run(_seed_user(pg, "locsrf@example.com"))
    asyncio.run(_insert_token(pg, user_id, "rt_locsrf"))
    access = encode_access(uuid.UUID(user_id), "ru", 900, secret=JWT_SECRET)
    with _client(pg) as client:
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access}"},
            cookies={"cp_refresh": "rt_locsrf"},
        )
    assert response.status_code == 403
