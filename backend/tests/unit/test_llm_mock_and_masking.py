"""S2.0 unit: Mock-детерминизм, отсутствие сетевых вызовов, MaskingPolicy STRICT."""

import socket
from typing import Any

import pytest
from pydantic import BaseModel

from app.llm.masking import MaskingPolicy, mask_text
from app.llm.providers.mock import MockProvider


class _Answer(BaseModel):
    answer: str


SYSTEM = "extract facts"
USER = "Senior Python developer at Acme Corp, worked with FastAPI and PostgreSQL"
MODEL = "mock-extractor-v1"


def _cassette_key() -> str:
    return MockProvider.cassette_key(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)


class TestMockDeterminism:
    async def test_same_input_same_output(self) -> None:
        mock = MockProvider({_cassette_key(): '{"answer": "stable"}'})
        first = await mock.complete(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)
        second = await mock.complete(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)
        assert first.content == second.content == '{"answer": "stable"}'
        assert first.usage == second.usage

    async def test_key_depends_on_all_inputs(self) -> None:
        base = dict(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)
        different_user = dict(base, user="other")
        different_model = dict(base, model="other-model")
        different_schema = dict(base, response_schema=None)
        key = MockProvider.cassette_key(**base)  # type: ignore[arg-type]
        assert MockProvider.cassette_key(**different_user) != key  # type: ignore[arg-type]
        assert MockProvider.cassette_key(**different_model) != key  # type: ignore[arg-type]
        assert MockProvider.cassette_key(**different_schema) != key  # type: ignore[arg-type]

    async def test_no_network_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Любая попытка открыть socket → тест падает (Mock не ходит в сеть)."""

        def _blocked(*args: Any, **kwargs: Any) -> Any:
            msg = "network access attempted by MockProvider"
            raise AssertionError(msg)

        monkeypatch.setattr(socket, "create_connection", _blocked)
        monkeypatch.setattr(socket, "socket", _blocked)
        mock = MockProvider({_cassette_key(): '{"answer": "offline"}'})
        response = await mock.complete(
            system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL
        )
        assert response.content == '{"answer": "offline"}'

    async def test_no_http_imports_in_mock_module(self) -> None:
        """Проверка по исходнику: httpx/aiohttp/requests не импортируются провайдером."""
        import inspect

        from app.llm.providers import mock as mock_module

        source = inspect.getsource(mock_module)
        for forbidden in ("import httpx", "import aiohttp", "import requests", "import openai"):
            assert forbidden not in source


class TestMaskingStrict:
    def test_masks_email_and_phone(self) -> None:
        text = "Contact: john.doe@example.com or +7 912 345 67 89"
        masked = mask_text(text)
        assert "john.doe@example.com" not in masked
        assert "912 345 67 89" not in masked
        assert "[EMAIL]" in masked and "[PHONE]" in masked

    def test_masks_person_name_pattern(self) -> None:
        masked = mask_text("Разработчик Ivan Petrov работал в команде")
        assert "Ivan Petrov" not in masked
        assert "[NAME]" in masked

    def test_does_not_mask_skills_companies_tech(self) -> None:
        text = (
            "Опыт: Python, FastAPI, PostgreSQL; компания Acme Corp; "
            "стек: Docker и Redis; Python разработчик"
        )
        masked = mask_text(text)
        for keep in ("Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "Acme Corp"):
            assert keep in masked, keep

    def test_debug_content_flag_is_distinct_policy(self) -> None:
        """DEBUG_CONTENT существует как политика; включение — только явным флагом (S2.6)."""
        assert MaskingPolicy.STRICT != MaskingPolicy.DEBUG_CONTENT
        assert MaskingPolicy.STRICT.value == "strict"
