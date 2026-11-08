"""S1.0 smoke: liveness-проба и доступность OpenAPI-схемы."""


def test_health_returns_ok(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_is_served(client) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"].startswith("3.")
