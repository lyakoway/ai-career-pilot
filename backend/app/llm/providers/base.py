"""Контракт LLM-провайдера (AI_DESIGN §2). Реализации: mock (S2.0);
openai_compat — будущий этап (Q-4=A: ADR-022 + чек-лист SECURITY §8)."""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

__all__ = ["Provider", "ProviderError", "ProviderResponse"]


@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    usage: Usage
    raw: dict[str, str] = field(default_factory=dict)


class ProviderError(Exception):
    """Сбой провайдера (сеть/формат) — триггер fallback-цепочки."""


class Provider(Protocol):
    """Единая точка вызова модели; name — идентификатор для routing."""

    name: str

    async def complete(
        self, *, system: str, user: str, response_schema: type[BaseModel] | None, model: str
    ) -> ProviderResponse: ...
