"""FastAPI application entry point (composition root)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.graph import (
    build_agent_graph,
    checkpointer_path_from_url,
    close_checkpointer,
    open_checkpointer,
)
from app.api import chat, health, products
from app.catalog.adapter import JsonCatalogAdapter, default_catalog_dir
from app.config import Settings, get_settings
from app.db import create_engine_and_sessionmaker, init_db
from app.gateway.client import ChatClient, create_chat_client
from app.gateway.errors import GatewayConfigError
from app.logging_config import configure_logging
from app.request_logging import RequestLoggingMiddleware

APP_VERSION = "0.1.0"


def _error_body(exc: Exception) -> dict[str, str]:
    """Errors use the {code, message} JSON shape (System Design 3.7)."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and {"code", "message"} <= set(detail):
        return {"code": detail["code"], "message": detail["message"]}
    return {"code": "http_error", "message": str(detail or exc)}


def create_app(
    settings: Settings | None = None, chat_client: ChatClient | None = None
) -> FastAPI:
    """Build the FastAPI application for the given (or ambient) settings.

    ``chat_client`` is the injection seam for tests and alternative builds
    (ADR-001); when omitted the real provider facade is constructed from
    settings, degrading to a "model not configured" chat stream when the
    provider config is unusable (e.g. missing LLM_API_KEY).
    """
    configure_logging()
    resolved_settings = settings if settings is not None else get_settings()
    engine, sessionmaker = create_engine_and_sessionmaker(resolved_settings.database_url)

    graph = None
    client_error: str | None = None
    if chat_client is not None:
        graph = build_agent_graph(client=chat_client)
    else:
        try:
            client = create_chat_client(resolved_settings)
            graph = build_agent_graph(client=client)
        except GatewayConfigError as exc:
            client_error = str(exc)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await init_db(engine)
        checkpointer = None
        if graph is not None:
            checkpoint_path = checkpointer_path_from_url(
                resolved_settings.checkpoint_db_url
            )
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpointer = await open_checkpointer(checkpoint_path)
            graph.checkpointer = checkpointer
        try:
            yield
        finally:
            if checkpointer is not None:
                await close_checkpointer(checkpointer)
            await engine.dispose()

    app = FastAPI(
        title="ShopMate API",
        version=APP_VERSION,
        description="Backend for the ShopMate conversational shopping assistant.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{resolved_settings.frontend_port}"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.state.settings = resolved_settings
    app.state.catalog = JsonCatalogAdapter()
    app.state.graph = graph
    app.state.client_error = client_error
    app.state.sessionmaker = sessionmaker
    app.state.engine = engine
    app.include_router(health.router, prefix="/api")
    app.include_router(products.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

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
