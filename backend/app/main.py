"""FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, products
from app.catalog.adapter import JsonCatalogAdapter, default_catalog_dir
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.request_logging import RequestLoggingMiddleware

APP_VERSION = "0.1.0"


def _error_body(exc: Exception) -> dict[str, str]:
    """Errors use the {code, message} JSON shape (System Design 3.7)."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and {"code", "message"} <= set(detail):
        return {"code": detail["code"], "message": detail["message"]}
    return {"code": "http_error", "message": str(detail or exc)}


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
    app.state.catalog = JsonCatalogAdapter()
    app.include_router(health.router, prefix="/api")
    app.include_router(products.router, prefix="/api")

    images_dir = default_catalog_dir() / "images"
    if images_dir.is_dir():
        app.mount(
            "/static/catalog/images",
            StaticFiles(directory=images_dir),
            name="catalog-images",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_to_code_shape(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_to_code_shape(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": "validation_error", "message": str(exc)},
        )

    return app


app = create_app()
