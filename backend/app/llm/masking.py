"""MaskingPolicy: что уходит наружу (в трейсы/Langfuse), AI_DESIGN §9.3.

STRICT (дефолт): маскируются email / телефон / ФИО-паттерны;
НЕ маскируются навыки, компании, технологии — иначе extraction сломается
(решение ревью Stage 2-плана §2.1). DEBUG_CONTENT — контент в trace при
явном флаге (WARNING при старте; использование — S2.6, Langfuse).
"""

import re
from enum import StrEnum

__all__ = ["MaskingPolicy", "mask_text"]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+7|\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b")
# ФИО-паттерн: 2–3 капитализированных слова подряд (русская/латинская),
# не в начале предложения-заголовка и не часть техно-терминов (Python и т.п.)
# — техно-термины проверяются по словарю ниже.
_TECH_WORDS = frozenset(
    {
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
        "Kubernetes",
        "Redis",
        "LangGraph",
        "Langfuse",
        "PyTorch",
        "TensorFlow",
        "React",
        "TypeScript",
        "JavaScript",
        "Postgres",
        "Mongo",
        "Kafka",
        "Git",
        "Linux",
        "Nginx",
    }
)
_NAME_RE = re.compile(
    r"\b([A-ZА-ЯЁ][a-zа-яё]+)\s+([A-ZА-ЯЁ][a-zа-яё]+)"
    r"(?:\s+([A-ZА-ЯЁ][a-zа-яё]+))?\b"
)
# Корп-суффиксы: "Acme Corp" — компания, не ФИО (решение ревью §2.1)
_CORP_SUFFIXES = frozenset(
    {
        "Corp",
        "Inc",
        "Ltd",
        "LLC",
        "GmbH",
        "AG",
        "Co",
        "Company",
        "Companies",
        "Labs",
        "Lab",
        "Technologies",
        "Technology",
        "Systems",
        "Solutions",
        "Group",
        "AI",
        "Digital",
        "Software",
        "Games",
        "Media",
        "Bank",
        "ООО",
        "ОАО",
        "АО",
        "ЗАО",
    }
)


class MaskingPolicy(StrEnum):
    """STRICT — метаданные наружу, PII-паттерны маскируются всегда."""

    STRICT = "strict"
    DEBUG_CONTENT = "debug_content"  # отдельный флаг, WARNING при старте (S2.6)


def mask_text(text: str) -> str:
    """Замаскировать PII в тексте (email → [EMAIL], телефон → [PHONE],
    ФИО-паттерн → [NAME]; навыки/компании/технологии не трогаются)."""
    masked = _EMAIL_RE.sub("[EMAIL]", text)
    masked = _PHONE_RE.sub("[PHONE]", masked)
    masked = _mask_names(masked)
    return masked


def _mask_names(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        words = [w for w in match.groups() if w]
        # Пропускаем техно-термины и компании (корп-суффикс в любом слове)
        if any(w in _TECH_WORDS for w in words) or any(w in _CORP_SUFFIXES for w in words):
            return match.group(0)
        return "[NAME]"

    return _NAME_RE.sub(_replace, text)
