"""LLMClient: routing роль→цепочка провайдеров, fallback, retry structured output.

Контракт AI_DESIGN §2 (complete без embed — Q-7, Stage 4). Retry×1 при
невалидном JSON/schema с diagnostic-сообщением; исчерпание цепочки →
ExternalServiceError (ARCHITECTURE §7.1: TransientExternalError).
"""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import ExternalServiceError
from app.llm.masking import MaskingPolicy
from app.llm.providers.base import Provider, ProviderError, Usage
from app.llm.providers.mock import MockProvider, mock_cost_usd

__all__ = ["Completion", "LLMClient", "build_llm_client"]

_VALID_ROLES = frozenset({"extraction", "evaluation"})


@dataclass(frozen=True)
class Completion:
    content: str
    parsed: BaseModel | None
    usage: Usage
    cost_usd: Decimal
    provider: str
    model: str
    fallbacks: list[str] = field(default_factory=list)
    trace_id: str | None = None


def _parse_schema(content: str, schema: type[BaseModel]) -> BaseModel:
    """json-разбор + Pydantic-валидация (без retry — см. client)."""
    import json

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        msg = f"invalid JSON from LLM: {exc.msg} at pos {exc.pos}"
        raise ValueError(msg) from exc
    return schema.model_validate(data)


class _ClientProtocol(Protocol):
    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
        masking: MaskingPolicy = MaskingPolicy.STRICT,
    ) -> Completion: ...


class LLMClient:
    """Роли задач → цепочки provider:model из Settings (S2.0: роль extraction)."""

    def __init__(self, settings: Settings, providers: dict[str, Provider]) -> None:
        self._settings = settings
        self._providers = providers
        self._role_chains: dict[str, list[tuple[str, str]]] = {
            "extraction": self._parse_chain(settings.llm_roles__extraction),
        }

    def _parse_chain(self, chain: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for entry in chain.split(","):
            provider_name, _, model = entry.strip().partition(":")
            if provider_name not in self._providers:
                msg = f"LLM provider {provider_name!r} is not registered"
                raise ValueError(msg)
            result.append((provider_name, model))
        return result

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
        masking: MaskingPolicy = MaskingPolicy.STRICT,
    ) -> Completion:
        if role not in _VALID_ROLES:
            msg = f"unknown LLM role {role!r}; valid: {sorted(_VALID_ROLES)}"
            raise ValueError(msg)
        chain = self._role_chains[role]
        _ = masking  # используется в S2.6 (Langfuse-обёртка); контракт зафиксирован
        errors: list[str] = []
        attempted: list[str] = []
        for provider_name, model in chain:
            provider = self._providers[provider_name]
            for attempt in (1, 2):  # retry×1 только на невалидный JSON/schema
                started = time.perf_counter()
                try:
                    response = await provider.complete(
                        system=system, user=user, response_schema=response_schema, model=model
                    )
                except ProviderError as exc:
                    errors.append(f"{provider_name}: {exc}")
                    break  # сбой провайдера → следующий в цепочке
                parsed: BaseModel | None = None
                if response_schema is not None:
                    try:
                        parsed = _parse_schema(response.content, response_schema)
                    except (ValueError, ValidationError) as exc:
                        errors.append(f"{provider_name}: attempt{attempt}: {exc}")
                        if attempt == 1:
                            continue  # retry с diagnostic уже в ошибке
                        break  # второй провал → следующий провайдер
                latency_ms = int((time.perf_counter() - started) * 1000)
                _ = latency_ms  # в ai_invocations пишет вызывающий (S2.4)
                attempted.append(provider_name)
                return Completion(
                    content=response.content,
                    parsed=parsed,
                    usage=response.usage,
                    cost_usd=mock_cost_usd(model, response.usage),
                    provider=provider_name,
                    model=model,
                    fallbacks=attempted[1:],
                )
        msg = f"all providers failed for role {role!r}: {'; '.join(errors)}"
        raise ExternalServiceError(msg) from None


def build_llm_client(settings: Settings) -> LLMClient:
    """Фабрика клиента с реестром доступных провайдеров (S2.0: только mock)."""
    providers: dict[str, Provider] = {
        MockProvider.name: MockProvider(),
    }
    return LLMClient(settings, providers)
