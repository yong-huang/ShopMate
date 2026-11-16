"""Shared test fixtures."""

import pytest

# Every configuration variable from System Design section 3.9. Tests clear
# these so behavior is hermetic regardless of the developer's shell env.
SECTION_39_ENV_VARS = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_CHAT_MODEL",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "DATABASE_URL",
    "SESSION_IDLE_TTL_MIN",
    "BACKEND_PORT",
    "FRONTEND_PORT",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all ShopMate env vars so Settings defaults apply hermetically."""
    for var in SECTION_39_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
