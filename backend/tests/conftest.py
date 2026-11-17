"""Shared test fixtures."""

import json
import logging

import pytest

from app.logging_config import JsonLineFormatter, LOGGER_NAME

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
    "CHECKPOINT_DB_URL",
    "SESSION_IDLE_TTL_MIN",
    "PROFILE_INJECTION_MAX_CHARS",
    "BACKEND_PORT",
    "FRONTEND_PORT",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all ShopMate env vars so Settings defaults apply hermetically."""
    for var in SECTION_39_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class JsonLineCapture(logging.Handler):
    """Capture the JSON lines the application logger emits, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.formatter = JsonLineFormatter()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def parsed(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]

    def of_event(self, event: str) -> list[dict]:
        return [p for p in self.parsed if p["event"] == event]


@pytest.fixture
def json_lines() -> JsonLineCapture:
    """Attach a JSON-line capture handler to the ShopMate logger."""
    capture = JsonLineCapture()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(capture)
    try:
        yield capture
    finally:
        logger.removeHandler(capture)
