"""S2.0 unit: LLMClient — routing, fallback, retry, unknown role, cost."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import ExternalServiceError
from app.llm.client import LLMClient, build_llm_client
from app.llm.providers.base import ProviderError, ProviderResponse, Usage
from app.llm.providers.mock import MockProvider

SYSTEM = "You are a fact extractor."
USER = "Resume: Senior Python developer at Acme Corp."
MODEL = "mock-extractor-v1"


class _Answer(BaseModel):
    answer: str


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://x@l/x",
        redis_url="redis://x",
        jwt_secret="x" * 48,
        ratelimit_hmac_key="y" * 48,
        **overrides,
    )


def _client_with_cassette(content: str, **overrides: Any) -> LLMClient:
    """Клиент с одной кассетой под текущий вход."""
    mock = MockProvider()
    key = MockProvider.cassette_key(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)
    mock._cassettes[key] = content
    settings = _settings(llm_roles__extraction=f"mock:{MODEL}", **overrides)
    return LLMClient(settings, {"mock": mock})


class _FailingFirst(MockProvider):
    """Первый провайдер цепочки всегда падает (тест fallback)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def complete(
        self, *, system: str, user: str, response_schema: type[BaseModel] | None, model: str
    ) -> ProviderResponse:
        self.calls += 1
        msg = "injected provider failure"
        raise ProviderError(msg)


async def test_routing_and_parsed_output() -> None:
    client = _client_with_cassette('{"answer": "42"}')
    completion = await client.complete(
        role="extraction", system=SYSTEM, user=USER, response_schema=_Answer
    )
    assert completion.provider == "mock"
    assert completion.model == MODEL
    assert isinstance(completion.parsed, _Answer)
    assert completion.parsed.answer == "42"  # type: ignore[attr-defined]
    assert completion.usage.tokens_in > 0 and completion.usage.tokens_out > 0
    assert completion.cost_usd == Decimal("0")


async def test_fallback_to_second_provider() -> None:
    failing = _FailingFirst()
    ok_mock = MockProvider()
    key = MockProvider.cassette_key(system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL)
    ok_mock._cassettes[key] = '{"answer": "fallback"}'
    settings = _settings(llm_roles__extraction=f"mock:{MODEL},mock2:{MODEL}")

    # Два провайдера mock с одинаковым name невозможно в dict — используем класс-обёртку
    class _Second(MockProvider):
        name = "mock2"

    second = _Second()
    second._cassettes[key] = '{"answer": "fallback"}'
    client = LLMClient(settings, {"mock": failing, "mock2": second})
    completion = await client.complete(
        role="extraction", system=SYSTEM, user=USER, response_schema=_Answer
    )
    assert completion.provider == "mock2"
    assert completion.parsed.answer == "fallback"  # type: ignore[attr-defined]
    assert failing.calls == 1


async def test_retry_on_invalid_json_then_success() -> None:
    """Невалидный JSON → retry×1 → (кассета отдаёт валидный со 2-й попытки).

    Реализация: провайдер с очередью ответов [битый, валидный]."""
    queue = ["{not json", '{"answer": "after-retry"}']
    base_key = MockProvider.cassette_key(
        system=SYSTEM, user=USER, response_schema=_Answer, model=MODEL
    )

    class _Flaky(MockProvider):
        name = "mock"

        def __init__(self) -> None:
            super().__init__()
            self.attempt = 0

        async def complete(
            self, *, system: str, user: str, response_schema: type[BaseModel] | None, model: str
        ) -> ProviderResponse:
            self.attempt += 1
            content = queue[min(self.attempt - 1, len(queue) - 1)]
            return ProviderResponse(
                content=content, usage=Usage(tokens_in=10, tokens_out=5), raw={}
            )

    _ = base_key
    settings = _settings(llm_roles__extraction=f"mock:{MODEL}")
    client = LLMClient(settings, {"mock": _Flaky()})
    completion = await client.complete(
        role="extraction", system=SYSTEM, user=USER, response_schema=_Answer
    )
    assert completion.parsed.answer == "after-retry"  # type: ignore[attr-defined]


async def test_all_providers_failed_raises_external() -> None:
    failing = _FailingFirst()
    settings = _settings(llm_roles__extraction=f"mock:{MODEL}")
    client = LLMClient(settings, {"mock": failing})
    with pytest.raises(ExternalServiceError, match="all providers failed"):
        await client.complete(role="extraction", system=SYSTEM, user=USER)


async def test_unknown_role_rejected() -> None:
    client = _client_with_cassette('{"answer": "x"}')
    with pytest.raises(ValueError, match="unknown LLM role"):
        await client.complete(role="nonexistent", system=SYSTEM, user=USER)


async def test_build_client_default_settings_ok() -> None:
    client = build_llm_client(_settings())
    assert isinstance(client, LLMClient)


async def test_unregistered_provider_fails_construction() -> None:
    settings = _settings(llm_roles__extraction="openai:g-4")
    with pytest.raises(ValueError, match="not registered"):
        LLMClient(settings, {"mock": MockProvider()})


async def test_missing_cassette_is_explicit_error() -> None:
    """Нет кассеты → ProviderError (не случайный ответ) → ExternalServiceError."""
    client = build_llm_client(_settings())
    with pytest.raises(ExternalServiceError):
        await client.complete(
            role="extraction", system=SYSTEM, user="unknown input", response_schema=_Answer
        )
