"""Pydantic-схемы structured outputs LLM (AI_DESIGN §2).

FactsExtraction v1 — совместима с profile_facts (DATABASE §3.3, S2.4):
type ∈ {skill, experience, project, education}; payload — словарь по типу;
quote обязательна (evidence binding — S2.4 проверит её в тексте резюме).
"""

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["ExtractedFact", "FactsExtraction"]

FactType = Literal["skill", "experience", "project", "education"]


class ExtractedFact(BaseModel):
    """Один факт, извлечённый из резюме. quote — дословная цитата-источник."""

    type: FactType
    payload: dict[str, str | int | float | None] = Field(
        description="Нормализуемое значение: skill — {skill}, experience — "
        "{company, title, start, end}, project — {name, highlights}, "
        "education — {institution, program, start, end}"
    )
    quote: str = Field(min_length=1, description="Дословная цитата из резюме")
    section: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class FactsExtraction(BaseModel):
    """Ответ роли extraction. Пустой список — валидный результат (нет фактов)."""

    facts: list[ExtractedFact]
