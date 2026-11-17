"""Session lifecycle endpoints (System Design 3.6 / 3.7, ADR-007).

- ``POST /api/sessions`` — start a new session; any of the shopper's still-
  open sessions is ended and its profile extraction is enqueued. The response
  and cookie reuse the chat endpoint's ``session`` semantics.
- ``POST /api/sessions/{id}/end`` — browser-unload ping; closes the session
  (idempotent) and enqueues its extraction.

Extraction always runs in a background task (``schedule_extraction``) — these
endpoints never wait on the model.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.api.identity import issue_cookie, resolve_identity
from app.db import get_or_create_session, SessionRow
from app.logging_config import get_logger
from app.profile.service import (
    end_session,
    open_sessions_for_user,
    schedule_extraction,
)

router = APIRouter(tags=["sessions"])
logger = get_logger()


@router.post("/sessions")
async def create_session(request: Request, response: Response) -> dict:
    """Start a new session; end + extract any prior open sessions."""
    sessionmaker = request.app.state.sessionmaker
    cookie_sid, issued_sid = await resolve_identity(request)
    if issued_sid is not None:
        issue_cookie(response, issued_sid)

    ended_sessions: list[str] = []
    async with sessionmaker() as db:
        for row in await open_sessions_for_user(db, cookie_sid):
            row.ended_at = datetime.now(UTC)
            ended_sessions.append(row.id)
        session_id = uuid.uuid4().hex
        await get_or_create_session(db, session_id, cookie_sid)
        await db.commit()

    _schedule_extractions(request, ended_sessions)
    return {"session_id": session_id, "user_id": cookie_sid}


@router.post("/sessions/{session_id}/end")
async def end_session_ping(
    session_id: str, request: Request, response: Response
) -> Response:
    """Browser-unload ping: close the session (idempotent) + extract."""
    cookie_sid, issued_sid = await resolve_identity(request)
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    sessionmaker = request.app.state.sessionmaker

    ended = await end_session(sessionmaker, session_id)
    if ended:
        _schedule_extractions(request, [session_id])
        return JSONResponse(content={"status": "ended", "session_id": session_id})
    # Idempotent when already ended; unknown ids are an honest 404.
    async with sessionmaker() as db:
        known = await db.get(SessionRow, session_id)
    if known is None:
        return JSONResponse(
            status_code=404,
            content={
                "code": "not_found",
                "message": f"Unknown session {session_id}",
            },
        )
    return JSONResponse(content={"status": "already_ended", "session_id": session_id})


def _schedule_extractions(request: Request, session_ids: list[str]) -> None:
    """Enqueue extraction for ended sessions (never awaited on the path)."""
    client = request.app.state.extraction_client
    if client is None or not session_ids:
        return
    for session_id in session_ids:
        logger.info(
            "profile_extraction_scheduled", extra={"session_id": session_id}
        )
        schedule_extraction(request.app.state.sessionmaker, client, session_id)
