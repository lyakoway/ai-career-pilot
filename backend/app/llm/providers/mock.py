"""Детерминированный Mock-провайдер (Q-4=A: без сетевых вызовов, без SDK).

Кассеты: JSON-словарь {cassette_key: response_content}, где ключ —
sha256(system + "\\0" + user + "\\0" + schema_name + "\\0" + model).
Same input → same output (воспроизводимость тестов и S2.7-baseline).
Нет кассеты → ProviderError (явная ошибка, не «случайный» ответ).
"""

import hashlib
from typing import Any

from pydantic import BaseModel

from app.llm.providers.base import ProviderError, ProviderResponse, Usage

__all__ = ["MockProvider"]

_MOCK_PRICING_PER_1M = {"mock-extractor-v1": (0.0, 0.0)}  # (in, out) USD; Mock бесплатен


class MockProvider:
    """name='mock'; кассеты инжектируются конструктором (фикстуры тестов)."""

    name = "mock"

    def __init__(self, cassettes: dict[str, str] | None = None) -> None:
        self._cassettes = cassettes or {}

    @staticmethod
    def cassette_key(
        *, system: str, user: str, response_schema: type[BaseModel] | None, model: str
    ) -> str:
        schema_name = response_schema.__name__ if response_schema else "-"
        raw = f"{system}\0{user}\0{schema_name}\0{model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def complete(
        self, *, system: str, user: str, response_schema: type[BaseModel] | None, model: str
    ) -> ProviderResponse:
        key = self.cassette_key(
            system=system, user=user, response_schema=response_schema, model=model
        )
        if key not in self._cassettes:
            msg = f"mock: no cassette for key={key[:12]} (model={model})"
            raise ProviderError(msg)
        content = self._cassettes[key]
        # Синтетический usage: детерминирован от длины контента
        tokens_out = max(1, len(content) // 4)
        tokens_in = max(1, (len(system) + len(user)) // 4)
        return ProviderResponse(
            content=content,
            usage=Usage(tokens_in=tokens_in, tokens_out=tokens_out),
            raw={"cassette": key[:12], "provider": self.name, "model": model},
        )


def mock_cost_usd(model: str, usage: Usage) -> Any:
    """Тариф Mock — 0 USD (таблица тарифов реальных моделей — будущий этап)."""
    price_in, price_out = _MOCK_PRICING_PER_1M.get(model, (0.0, 0.0))
    from decimal import Decimal

    return (
        Decimal(str(price_in)) * usage.tokens_in / 1_000_000
        + Decimal(str(price_out)) * usage.tokens_out / 1_000_000
    )
