"""S1.1: /ready — readiness по фейкам зависимостей; /health без зависимостей."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeEngine, FakeRedis


def make_client(db_healthy: bool = True, redis_healthy: bool = True) -> TestClient:
    return TestClient(
        create_app(db_engine=FakeEngine(db_healthy), redis_client=FakeRedis(redis_healthy))
    )


def test_ready_ok_when_dependencies_healthy() -> None:
    with make_client() as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "checks": {"database": True, "redis": True}}


def test_ready_503_when_redis_down() -> None:
    with make_client(redis_healthy=False) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"database": True, "redis": False}


def test_ready_503_when_database_down() -> None:
    with make_client(db_healthy=False) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": False, "redis": True}


def test_ready_503_when_both_down() -> None:
    with make_client(db_healthy=False, redis_healthy=False) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": False, "redis": False}


def test_health_untouched_and_dependency_free() -> None:
    with make_client(db_healthy=False, redis_healthy=False) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
