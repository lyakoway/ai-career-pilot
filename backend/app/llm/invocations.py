"""Запись ai_invocations (DATABASE §3.12) — в транзакции вызывающего кода.

Append-only: cp_app имеет только SELECT/INSERT (миграция 0002 + тест).
purpose-словарь — по DATABASE: resume_parse | match_score | copilot_node |
embedding | fact_check | evaluation.
"""

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

__all__ = ["AIInvocationPurpose", "record_invocation"]

AIInvocationPurpose = str  # словарь DATABASE §3.12 (см. модуль docstring)


async def record_invocation(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    purpose: AIInvocationPurpose,
    provider: str,
    model: str,
    prompt_version: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: Decimal,
    latency_ms: int,
    trace_id: str | None = None,
    status: str = "ok",
) -> uuid.UUID:
    """INSERT ai_invocations; без commit — коммитит транзакция вызывающего."""
    invocation_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO ai_invocations "
            "(id, user_id, purpose, provider, model, prompt_version, "
            " tokens_in, tokens_out, cost_usd, latency_ms, trace_id, status) "
            "VALUES (:id, :user_id, :purpose, :provider, :model, :prompt_version, "
            " :tokens_in, :tokens_out, :cost_usd, :latency_ms, :trace_id, :status)"
        ),
        {
            "id": invocation_id,
            "user_id": user_id,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "trace_id": trace_id,
            "status": status,
        },
    )
    return invocation_id
