"""Typed environment configuration (System Design section 3.9).

All behavior switches are env vars; ``backend/.env.example`` documents the
full list and is the source of truth for operator-facing configuration.
"""

from functools import lru_cache
from typing import Literal

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
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen3:8b"
    embedding_provider: Literal["auto", "ollama", "glm", "none"] = "auto"
    embedding_model: str = "bge-m3"
    database_url: str = "sqlite+aiosqlite:///./data/shopmate.db"
    session_idle_ttl_min: int = 15
    backend_port: int = 8000
    frontend_port: int = 3000


@lru_cache
def get_settings() -> Settings:
    """Build the ambient settings (cached for the process lifetime)."""
    return Settings()
