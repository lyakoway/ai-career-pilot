"""S2.0: валидация CP_LLM_ROLES__EXTRACTION (fail-fast) на реальном старте Settings."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _base_env() -> dict[str, str]:
    return {
        "CP_DATABASE_URL": "postgresql+asyncpg://x@l/x",
        "CP_REDIS_URL": "redis://x",
        "CP_JWT_SECRET": "x" * 48,
        "CP_RATELIMIT_HMAC_KEY": "y" * 48,
    }


def test_default_role_is_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.llm_roles__extraction == "mock:mock-extractor-v1"
    assert settings.llm_debug_content is False


@pytest.mark.parametrize(
    "chain",
    [
        "mock",  # нет model
        "mock:",  # пустая model
        ":model",  # пустой provider
        "mock:model:extra",  # лишнее двоеточие
        "  ",  # пустая цепочка
        "a:b,,c:d",  # пустой элемент
    ],
)
def test_invalid_chain_fails_fast(monkeypatch: pytest.MonkeyPatch, chain: str) -> None:
    env = dict(_base_env(), **{"CP_LLM_ROLES__EXTRACTION": chain})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError, match=r"provider:model|must not be empty"):
        Settings(_env_file=None)


def test_valid_chain_with_fallback_list(monkeypatch: pytest.MonkeyPatch) -> None:
    env = dict(_base_env(), **{"CP_LLM_ROLES__EXTRACTION": "mock:m1, mock:m2"})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.llm_roles__extraction == "mock:m1, mock:m2"


def test_debug_content_flag_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    env = dict(_base_env(), **{"CP_LLM_DEBUG_CONTENT": "true"})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.llm_debug_content is True
