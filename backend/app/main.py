"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.request_logging import RequestLoggingMiddleware

APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application for the given (or ambient) settings."""
    configure_logging()
    resolved_settings = settings if settings is not None else get_settings()
    app = FastAPI(
        title="ShopMate API",
        version=APP_VERSION,
        description="Backend for the ShopMate conversational shopping assistant.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{resolved_settings.frontend_port}"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.state.settings = resolved_settings
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
