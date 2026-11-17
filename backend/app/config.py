"""Typed environment configuration (System Design section 3.9).

All behavior switches are env vars; ``backend/.env.example`` documents the
full list and is the source of truth for operator-facing configuration.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["glm", "openai", "ollama"] = "glm"
    llm_model: str = "glm-5.3-flash"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    # Consecutive tool-enabled attempts without a valid tool call before the
    # gateway raises ToolCallDegradationError (System Design 3.1 degradation).
    llm_tool_degradation_attempts: int = 3
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen3:8b"
    # T08 eval harness (docs/eval-plan.md §3.2): judge provider/model
    # overrides. Both default to the configured default chat provider/model
    # (the eval baseline judge is the baseline provider).
    judge_provider: Literal["glm", "openai", "ollama"] | None = None
    judge_model: str | None = None

    @field_validator("judge_provider", "judge_model", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        """An empty env var (``JUDGE_MODEL=``) means "not overridden"."""
        if value == "":
            return None
        return value
    embedding_provider: Literal["auto", "ollama", "glm", "none"] = "auto"
    embedding_model: str = "bge-m3"
    database_url: str = "sqlite+aiosqlite:///./data/shopmate.db"
    # SQLite file for the LangGraph checkpointer (System Design 3.2): one
    # thread per session, so conversation state survives restarts.
    checkpoint_db_url: str = "sqlite+aiosqlite:///./data/checkpoints.db"
    session_idle_ttl_min: int = 15
    # Profile injection cap (System Design 3.6): the rendered profile block
    # is truncated to this many characters (~300 tokens at ~4 chars/token).
    profile_injection_max_chars: int = 1200
    backend_port: int = 8000
    frontend_port: int = 3000


@lru_cache
def get_settings() -> Settings:
    """Build the ambient settings (cached for the process lifetime)."""
    return Settings()
