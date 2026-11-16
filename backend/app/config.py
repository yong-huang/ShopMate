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


@lru_cache
def get_settings() -> Settings:
    """Build the ambient settings (cached for the process lifetime)."""
    return Settings()
