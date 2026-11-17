"""Request-scoped structured logging middleware (NFR-4).

Every HTTP request emits one `http_request` JSON record with method, path,
status code, and latency. The middleware also opens the request-scoped turn
context (see `app.turnlog`) so chat endpoints can attach tool hops, token
usage, and validation outcomes that land in a per-turn `chat_turn` record.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import get_logger
from app.turnlog import request_scope

logger = get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit a structured access record and manage the per-request turn scope."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = uuid.uuid4().hex
        scope = request_scope(request_id=request_id)
        started = time.perf_counter()
        error: BaseException | None = None
        response: Response | None = None
        try:
            with scope:
                response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - re-raised below
            error = exc
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            status_code = response.status_code if response is not None else 500
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "status": "error" if error is not None else "ok",
                    **scope.correlation(),
                    **({"error_type": type(error).__name__} if error else {}),
                    **({"error": str(error)} if error else {}),
                },
            )
        return response
