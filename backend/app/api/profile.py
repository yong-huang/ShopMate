"""Profile control + interaction capture endpoints (System Design 3.6 / 3.7).

- ``GET    /api/profile`` — preferences panel data
- ``DELETE /api/profile`` — clear the profile entirely (interaction log
  included); nothing is re-injected afterwards
- ``POST   /api/products/{id}/view`` — lightweight interaction hook the
  product page fires when a shopper opens a product (§3.6 capture stage;
  cart/purchase events land here too via T06's ``record_interaction``)

Identity mirrors the chat endpoint: the anonymous ``shopmate_sid`` HttpOnly
cookie is issued on first contact and reused afterwards.
"""

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.catalog.schemas import ProductDetail
from app.db import clear_profile, get_or_create_user, record_interaction
from app.logging_config import get_logger
from app.api.chat import SESSION_COOKIE
from app.profile.service import get_profile_payload

router = APIRouter(tags=["profile"])
logger = get_logger()


async def _resolve_identity(request: Request) -> tuple[str, str | None]:
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


def _issue_cookie(response: Response, sid: str) -> None:
    """Attach the identity cookie (same semantics as the chat endpoint)."""
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=365 * 24 * 3600,
    )


@router.get("/profile")
async def read_profile(request: Request, response: Response) -> dict:
    """The shopper's profile payload (empty defaults before anything lands)."""
    user_id, issued_sid = await _resolve_identity(request)
    if issued_sid is not None:
        _issue_cookie(response, issued_sid)
    return await get_profile_payload(request.app.state.sessionmaker, user_id)


@router.delete("/profile")
async def delete_profile(request: Request, response: Response) -> dict:
    """Clear the profile completely — preferences and interaction log."""
    user_id, issued_sid = await _resolve_identity(request)
    if issued_sid is not None:
        _issue_cookie(response, issued_sid)
    async with request.app.state.sessionmaker() as db:
        cleared = await clear_profile(db, user_id)
        await db.commit()
    logger.info(
        "profile_cleared", extra={"user_id": user_id, "existed": cleared}
    )
    return {"status": "cleared"}


@router.post("/products/{product_id}/view")
async def record_product_view(request: Request, product_id: str, response: Response):
    """Capture one product-view interaction (§3.6 capture stage).

    Storefront product pages fire this on mount; it is deliberately
    lightweight (one upsert + one JSON append) and never blocks rendering.
    """
    # Identity first: a shopper who opens a stale product link still gets
    # their anonymous id issued for the rest of the visit.
    cookie_sid, issued_sid = await _resolve_identity(request)
    if issued_sid is not None:
        _issue_cookie(response, issued_sid)
    catalog: ProductDetail | None = request.app.state.catalog.get_product(product_id)
    if catalog is None:
        # A fresh JSONResponse bypasses the injected ``response`` above, so
        # the identity cookie is attached to the error body itself.
        error = JSONResponse(
            status_code=404,
            content={"code": "not_found", "message": f"Unknown product {product_id}"},
        )
        if issued_sid is not None:
            _issue_cookie(error, issued_sid)
        return error
    async with request.app.state.sessionmaker() as db:
        await record_interaction(
            db, user_id=cookie_sid, type="view", product_id=product_id
        )
        await db.commit()
    return {"status": "recorded", "product_id": product_id}
