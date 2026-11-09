"""S1.3: provisioning владельца на реальной PostgreSQL (через provision(), без subprocess)."""

import asyncio

from app.core.config import get_settings
from app.core.security.passwords import verify_password
from scripts.provision_owner import provision
from tests.conftest import _truncate, fetch_all

EMAIL = "owner-s13@example.com"
PASSWORD = "Str0ng-Passw0rd-Example"


def _cleanup(pg: dict[str, str]) -> None:
    asyncio.run(_truncate(pg["admin"]))


def test_provision_owner_creates_user_and_audit(pg: dict[str, str], monkeypatch) -> None:
    _cleanup(pg)
    monkeypatch.setenv("CP_DATABASE_URL", pg["app"])
    get_settings.cache_clear()
    try:
        assert asyncio.run(provision(EMAIL, PASSWORD)) == 0

        users = fetch_all(
            pg["admin"], "SELECT id, password_hash FROM users WHERE email = :e", {"e": EMAIL}
        )
        assert len(users) == 1
        # Argon2id в БД, не plaintext; пароль верифицируется
        assert users[0]["password_hash"].startswith("$argon2id$")
        assert PASSWORD not in users[0]["password_hash"]
        assert verify_password(PASSWORD, users[0]["password_hash"]) is True

        audit = fetch_all(
            pg["admin"],
            "SELECT action, context FROM audit_events WHERE action = 'user.provisioned'",
        )
        assert len(audit) == 1
        assert audit[0]["context"] == {"via": "cli"}

        # Идемпотентность: повтор -> exit 1, без дублей пользователя и аудита
        assert asyncio.run(provision(EMAIL, PASSWORD)) == 1
        assert len(fetch_all(pg["admin"], "SELECT 1 AS x FROM users")) == 1
        assert (
            len(
                fetch_all(
                    pg["admin"],
                    "SELECT 1 AS x FROM audit_events WHERE action = 'user.provisioned'",
                )
            )
            == 1
        )
    finally:
        get_settings.cache_clear()
        _cleanup(pg)


def test_provision_owner_rejects_weak_password(pg: dict[str, str], monkeypatch) -> None:
    _cleanup(pg)
    monkeypatch.setenv("CP_DATABASE_URL", pg["app"])
    get_settings.cache_clear()
    try:
        assert asyncio.run(provision("weak@example.com", "1234567890")) == 2
        assert fetch_all(pg["admin"], "SELECT 1 AS x FROM users") == []
        assert fetch_all(pg["admin"], "SELECT 1 AS x FROM audit_events") == []
    finally:
        get_settings.cache_clear()
        _cleanup(pg)
