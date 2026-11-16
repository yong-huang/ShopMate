"""Tests for typed env configuration (System Design section 3.9).

These exercise the env seam: OS environment variables -> typed Settings.
"""

from pydantic import ValidationError
import pytest

from app.config import Settings


def test_settings_defaults_match_system_design_section_3_9() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "glm"
    assert settings.llm_model == "glm-5.3-flash"
    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_chat_model == "qwen3:8b"
    assert settings.embedding_provider == "auto"
    assert settings.embedding_model == "bge-m3"
    assert settings.database_url == "sqlite+aiosqlite:///./data/shopmate.db"
    assert settings.session_idle_ttl_min == 15
    assert settings.backend_port == 8000
    assert settings.frontend_port == 3000


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("SESSION_IDLE_TTL_MIN", "30")
    monkeypatch.setenv("BACKEND_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-5"
    assert settings.session_idle_ttl_min == 30
    assert settings.backend_port == 9000


def test_unknown_llm_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "bogus")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unknown_embedding_provider_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bogus")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
