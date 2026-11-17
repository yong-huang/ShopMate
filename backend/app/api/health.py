"""GET /api/health - liveness + model-provider and catalog-index status."""

from fastapi import APIRouter, Request

from app.gateway.status import ProviderStatus, provider_status

router = APIRouter(tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health")
async def health(request: Request) -> dict:
    """Liveness probe plus model-provider and catalog-index status (3.7)."""
    settings = request.app.state.settings
    index = getattr(request.app.state, "retrieval_index", None)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "provider": provider_status(settings).model_dump(),
        "index": {
            "ready": index is not None,
            "vectors_enabled": bool(index is not None and index.vectors_enabled),
        },
    }
