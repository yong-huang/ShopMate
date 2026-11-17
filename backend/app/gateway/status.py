"""Provider status resolution for the health endpoint.

Configuration-level only: reports what is configured (provider, model,
credentials), never dials the provider. Live connectivity checks are the
model gateway's job once chat lands.

Resolution rules (System Design sections 3.1 / 3.9):

- ``ollama`` needs no API key; the effective chat model is
  ``OLLAMA_CHAT_MODEL`` and the endpoint ``OLLAMA_BASE_URL``.
- Cloud providers (``glm`` / ``openai``) use ``LLM_MODEL`` and
  ``LLM_BASE_URL`` and require ``LLM_API_KEY``.
"""

from typing import Literal

from pydantic import BaseModel

from app.config import Settings


class ProviderStatus(BaseModel):
    """Configured model provider as reported by ``GET /api/health``."""

    name: str
    model: str
    base_url: str | None
    api_key_configured: bool
    status: Literal["configured", "not_configured"]


def provider_status(settings: Settings) -> ProviderStatus:
    """Resolve the configured provider from settings."""
    if settings.llm_provider == "ollama":
        return ProviderStatus(
            name="ollama",
            model=settings.ollama_chat_model,
            base_url=settings.ollama_base_url,
            api_key_configured=True,
            status="configured",
        )
    key_configured = bool(settings.llm_api_key)
    base_url = settings.llm_base_url or None  # empty env var means unset
    return ProviderStatus(
        name=settings.llm_provider,
        model=settings.llm_model,
        base_url=base_url,
        api_key_configured=key_configured,
        status="configured" if key_configured else "not_configured",
    )
