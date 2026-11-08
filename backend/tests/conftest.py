"""Общие фикстуры тестов backend."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Клиент против свежесобранного приложения (изоляция тестов)."""
    return TestClient(create_app())
