"""GET /api/health - liveness + configured model provider status."""

from fastapi import APIRouter, Request

from app.gateway.status import ProviderStatus, provider_status

router = APIRouter(tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health")
async def health(request: Request) -> dict:
    """Liveness probe plus a summary of the configured model provider."""
    settings = request.app.state.settings
    return {
        "status": "ok",
        "version": APP_VERSION,
        "provider": provider_status(settings).model_dump(),
    }
