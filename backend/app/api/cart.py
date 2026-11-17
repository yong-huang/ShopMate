"""Storefront cart + demo checkout endpoints (System Design 3.7, PRD FR-4/FR-5).

- ``GET    /api/cart`` — cart page/drawer data
- ``POST   /api/cart/items`` — storefront "Add to cart" button
- ``PATCH  /api/cart/items/{cart_item_id}`` — quantity update
- ``DELETE /api/cart/items/{cart_item_id}`` — remove line
- ``POST   /api/checkout`` — demo checkout from the cart page

All five call the SAME ``app.cart.service`` functions as Maya's cart tools,
so the storefront and chat can never disagree about cart state (FR-4).
Identity mirrors the other endpoints: the anonymous ``shopmate_sid`` HttpOnly
cookie is issued on first contact (app.api.identity). Errors use the
``{code, message}`` JSON shape (3.7) with the status the service mapped.
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.identity import issue_cookie, resolve_identity
from app.cart.service import CartError, add_item, cart_summary, checkout, remove_item, update_item_quantity

router = APIRouter(tags=["cart"])


class AddCartItemRequest(BaseModel):
    """POST /api/cart/items body (3.7)."""

    product_id: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    variant: dict[str, str] | None = None


class UpdateCartItemRequest(BaseModel):
    """PATCH /api/cart/items/{id} body."""

    quantity: int = Field(ge=1)


class CheckoutRequest(BaseModel):
    """POST /api/checkout body (demo — no payment fields)."""

    shipping_note: str | None = None


def _error(exc: CartError):
    """Map a CartError onto the 3.7 JSON error shape."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


@router.get("/cart")
async def read_cart(request: Request, response: Response) -> dict:
    """Cart page data: items with line prices and the cart total."""
    user_id, issued_sid = await resolve_identity(request)
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    async with request.app.state.sessionmaker() as db:
        return await cart_summary(db, request.app.state.catalog, user_id)


@router.post("/cart/items")
async def add_cart_item(
    body: AddCartItemRequest, request: Request, response: Response
):
    """Storefront "Add to cart" — same service call as the add_to_cart tool."""
    user_id, issued_sid = await resolve_identity(request)
    try:
        async with request.app.state.sessionmaker() as db:
            summary = await add_item(
                db,
                request.app.state.catalog,
                user_id,
                product_id=body.product_id,
                quantity=body.quantity,
                variant=body.variant,
            )
            await db.commit()
    except CartError as exc:
        error = _error(exc)
        if issued_sid is not None:
            issue_cookie(error, issued_sid)
        return error
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    return summary


@router.patch("/cart/items/{cart_item_id}")
async def update_cart_item(
    cart_item_id: int, body: UpdateCartItemRequest, request: Request, response: Response
):
    """Update one line's quantity."""
    user_id, issued_sid = await resolve_identity(request)
    try:
        async with request.app.state.sessionmaker() as db:
            summary = await update_item_quantity(
                db,
                request.app.state.catalog,
                user_id,
                cart_item_id,
                quantity=body.quantity,
            )
            await db.commit()
    except CartError as exc:
        error = _error(exc)
        if issued_sid is not None:
            issue_cookie(error, issued_sid)
        return error
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    return summary


@router.delete("/cart/items/{cart_item_id}")
async def delete_cart_item(
    cart_item_id: int, request: Request, response: Response
):
    """Remove one line from the cart."""
    user_id, issued_sid = await resolve_identity(request)
    try:
        async with request.app.state.sessionmaker() as db:
            summary = await remove_item(db, request.app.state.catalog, user_id, cart_item_id)
            await db.commit()
    except CartError as exc:
        error = _error(exc)
        if issued_sid is not None:
            issue_cookie(error, issued_sid)
        return error
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    return summary


@router.post("/checkout")
async def checkout_cart(body: CheckoutRequest, request: Request, response: Response):
    """Demo checkout (FR-5): snapshot order rows, clear the cart, return the id."""
    user_id, issued_sid = await resolve_identity(request)
    try:
        async with request.app.state.sessionmaker() as db:
            order = await checkout(
                db,
                request.app.state.catalog,
                user_id,
                shipping_note=body.shipping_note,
            )
            await db.commit()
    except CartError as exc:
        error = _error(exc)
        if issued_sid is not None:
            issue_cookie(error, issued_sid)
        return error
    if issued_sid is not None:
        issue_cookie(response, issued_sid)
    return order
