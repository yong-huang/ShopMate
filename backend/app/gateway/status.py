"""Provider status resolution for the health endpoint.

Configuration-level only: reports what is configured (provider, model,
credentials), never dials the provider. Live connectivity checks are the
model gateway's job once chat lands.
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
    return ProviderStatus(
        name=settings.llm_provider,
        model=settings.llm_model,
        base_url=None,
        api_key_configured=False,
        status="not_configured",
    )
