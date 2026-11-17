"""Anonymous cookie identity shared by the API endpoints (System Design 3.7).

The ``shopmate_sid`` HttpOnly cookie maps to a ``users`` row; it is issued on
first contact and reused afterwards. The chat endpoint (T04) holds its own
inline copy of the same semantics; the session/profile endpoints share this
module.
"""

import uuid

from fastapi import Request, Response

from app.api.chat import SESSION_COOKIE
from app.db import get_or_create_user


async def resolve_identity(request: Request) -> tuple[str, str | None]:
    """Map/issue the ``shopmate_sid`` cookie; return (sid, issued_sid).

    ``issued_sid`` is set only when a fresh cookie must be attached to the
    outgoing response — callers do that themselves so both the normal and
    the early-return (error JSON) paths carry the identity.
    """
    sessionmaker = request.app.state.sessionmaker
    issued_sid: str | None = None
    cookie_sid = request.cookies.get(SESSION_COOKIE)
    if cookie_sid is None:
        cookie_sid = issued_sid = uuid.uuid4().hex
    async with sessionmaker() as db:
        await get_or_create_user(db, cookie_sid)
        await db.commit()
    return cookie_sid, issued_sid


def issue_cookie(response: Response, sid: str) -> None:
    """Attach the identity cookie (same semantics as the chat endpoint)."""
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=365 * 24 * 3600,
    )
