"""The shared cart/checkout service (System Design 3.3, 3.7, 3.8).

Every cart mutation — from Maya's ``add_to_cart`` tool or the storefront
REST endpoints — flows through this module, so chat and the storefront can
never disagree about cart state (PRD FR-4). Checkout (FR-5) validates a
non-empty cart, writes ``orders`` + ``order_items`` with the purchase-time
``name_snapshot`` / ``unit_price`` frozen from the catalog, clears the cart,
and returns the order payload.

Validation failures raise :class:`CartError` with a stable ``code`` — the
tool layer turns it into an honest string for the model, the REST layer maps
it onto the ``{code, message}`` JSON error shape (3.7). Interaction events
(``add_to_cart`` / ``purchase``) are appended to the shopper's profile via
``app.db.record_interaction`` (§3.6 capture stage, T07 seam).

Callers pass in their own ``AsyncSession`` and ``CatalogAdapter``; the
functions flush only — the caller commits.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductDetail
from app.db import Cart, CartItem, Order, OrderItem, record_interaction

# HTTP status per error code (REST layer); the tool layer only uses ``code``.
ERROR_STATUS = {
    "product_not_found": 404,
    "cart_item_not_found": 404,
    "empty_cart": 409,
    "invalid_quantity": 422,
    "invalid_variant": 422,
    "out_of_stock": 422,
}

# Variant keys the schema supports; the catalog defines size lists via
# ``sizing.sizes`` (apparel) and exactly one colour per product via
# ``attributes["colour"]``.
VARIANT_KEYS = ("size", "color")

CURRENCY = "USD"


class CartError(Exception):
    """A cart validation failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return ERROR_STATUS.get(self.code, 422)


# --- Variant normalization (AC4.3: quantity + variant where catalog defines) --


def normalize_variant(product: ProductDetail, variant: Any) -> dict[str, str] | None:
    """Validate a requested ``{size?, color?}`` against the catalog record.

    Matching is case-insensitive; the stored value is the catalog's canonical
    casing. Unknown keys, sizes the product does not come in, and colours that
    do not match the product's single ``colour`` attribute are rejected.
    """
    if variant is None:
        return None
    if not isinstance(variant, dict):
        raise CartError(
            "invalid_variant", "variant must be an object with optional size/color."
        )
    unknown = sorted(set(variant) - set(VARIANT_KEYS))
    if unknown:
        raise CartError(
            "invalid_variant",
            f"Unknown variant key(s) {unknown}; supported: {list(VARIANT_KEYS)}.",
        )
    normalized: dict[str, str] = {}
    size = variant.get("size")
    if size not in (None, ""):
        sizes = [str(s) for s in ((product.sizing or {}).get("sizes") or [])]
        if not sizes:
            raise CartError(
                "invalid_variant",
                f"Product {product.id} does not come in sizes.",
            )
        wanted = str(size).strip().casefold()
        match = next((s for s in sizes if s.casefold() == wanted), None)
        if match is None:
            raise CartError(
                "invalid_variant",
                f"Product {product.id} comes in sizes {sizes}, not {size!r}.",
            )
        normalized["size"] = match
    color = variant.get("color")
    if color not in (None, ""):
        colour = product.attributes.get("colour")
        if colour is None:
            raise CartError(
                "invalid_variant",
                f"Product {product.id} has no colour variants in the catalog.",
            )
        if str(color).strip().casefold() != str(colour).casefold():
            raise CartError(
                "invalid_variant",
                f"Product {product.id} is only available in {colour!r}.",
            )
        normalized["color"] = str(colour)
    return normalized or None


def _validate_quantity(quantity: Any) -> int:
    """A positive integer count (bools are not counts)."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise CartError("invalid_quantity", "quantity must be a positive integer.")
    if quantity < 1:
        raise CartError("invalid_quantity", "quantity must be a positive integer.")
    return quantity


def _same_variant(a: dict[str, str] | None, b: dict[str, str] | None) -> bool:
    return (a or {}) == (b or {})


# --- Reads -------------------------------------------------------------------


async def get_or_create_cart(db: AsyncSession, user_id: str) -> Cart:
    """The shopper's cart row, creating it on first add (``carts.user_id`` PK)."""
    cart = await db.get(Cart, user_id)
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()
    return cart


async def _cart_lines(db: AsyncSession, user_id: str) -> list[CartItem]:
    query = (
        select(CartItem)
        .where(CartItem.cart_id == user_id)
        .order_by(CartItem.id)
    )
    return list((await db.execute(query)).scalars().all())


def _empty_summary() -> dict[str, Any]:
    return {"items": [], "item_count": 0, "total": 0.0, "currency": CURRENCY}


def _line_payload(item: CartItem, product: ProductDetail | None) -> dict[str, Any]:
    unit_price = product.price if product is not None else 0.0
    return {
        "cart_item_id": item.id,
        "product_id": item.product_id,
        "name_en": product.name_en if product else item.product_id,
        "name_zh": product.name_zh if product else item.product_id,
        "unit_price": unit_price,
        "currency": CURRENCY,
        "quantity": item.quantity,
        "variant": dict(item.variant) if item.variant else None,
        "line_total": round(unit_price * item.quantity, 2),
        "image": product.image if product else "",
    }


async def cart_summary(
    db: AsyncSession, catalog: CatalogAdapter, user_id: str
) -> dict[str, Any]:
    """The full cart payload (§3.3 get_cart / §3.7 GET /api/cart shape)."""
    lines = await _cart_lines(db, user_id)
    if not lines:
        return _empty_summary()
    items = [
        _line_payload(line, catalog.get_product(line.product_id)) for line in lines
    ]
    return {
        "items": items,
        "item_count": sum(item["quantity"] for item in items),
        "total": round(sum(item["line_total"] for item in items), 2),
        "currency": CURRENCY,
    }


# --- Mutations ---------------------------------------------------------------


async def add_item(
    db: AsyncSession,
    catalog: CatalogAdapter,
    user_id: str,
    *,
    product_id: str,
    quantity: Any = 1,
    variant: Any = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Add (or merge) one line; returns the fresh cart summary.

    Same product + same (normalized) variant merges into a single line —
    the cart never holds phantom duplicates for one explicit request (AC4.2).
    Quantity is validated against the demo stock (3.3).
    """
    product = catalog.get_product(product_id)
    if product is None:
        raise CartError(
            "product_not_found", f"No product with id {product_id!r} in the catalog."
        )
    count = _validate_quantity(quantity)
    normalized = normalize_variant(product, variant)
    if count > product.stock:
        raise CartError(
            "out_of_stock",
            f"Product {product.id} has {product.stock} in demo stock; "
            f"{count} requested.",
        )

    lines = await _cart_lines(db, user_id)
    existing = next(
        (
            line
            for line in lines
            if line.product_id == product_id
            and _same_variant(line.variant, normalized)
        ),
        None,
    )
    if existing is not None:
        merged = existing.quantity + count
        if merged > product.stock:
            raise CartError(
                "out_of_stock",
                f"Product {product.id} has {product.stock} in demo stock; "
                f"{merged} would exceed it.",
            )
        existing.quantity = merged
    else:
        db.add(
            CartItem(
                cart_id=user_id,
                product_id=product_id,
                quantity=count,
                variant=normalized,
            )
        )
    await get_or_create_cart(db, user_id)
    cart = await db.get(Cart, user_id)
    if cart is not None:
        cart.updated_at = datetime.now(UTC)
    await record_interaction(
        db,
        user_id,
        type="add_to_cart",
        product_id=product_id,
        session_id=session_id,
        quantity=count,
        variant=normalized,
    )
    await db.flush()
    return await cart_summary(db, catalog, user_id)


async def update_item_quantity(
    db: AsyncSession,
    catalog: CatalogAdapter,
    user_id: str,
    cart_item_id: int,
    *,
    quantity: Any,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resize one cart line (storefront PATCH path); returns the summary."""
    count = _validate_quantity(quantity)
    line = await db.get(CartItem, cart_item_id)
    if line is None or line.cart_id != user_id:
        raise CartError(
            "cart_item_not_found", f"No cart item {cart_item_id} in this cart."
        )
    product = catalog.get_product(line.product_id)
    if product is not None and count > product.stock:
        raise CartError(
            "out_of_stock",
            f"Product {line.product_id} has {product.stock} in demo stock; "
            f"{count} requested.",
        )
    line.quantity = count
    await db.flush()
    return await cart_summary(db, catalog, user_id)


async def remove_item(
    db: AsyncSession, catalog: CatalogAdapter, user_id: str, cart_item_id: int
) -> dict[str, Any]:
    """Delete one cart line; returns the fresh summary."""
    line = await db.get(CartItem, cart_item_id)
    if line is None or line.cart_id != user_id:
        raise CartError(
            "cart_item_not_found", f"No cart item {cart_item_id} in this cart."
        )
    await db.delete(line)
    await db.flush()
    return await cart_summary(db, catalog, user_id)


async def checkout(
    db: AsyncSession,
    catalog: CatalogAdapter,
    user_id: str,
    *,
    session_id: str | None = None,
    shipping_note: str | None = None,
) -> dict[str, Any]:
    """Demo checkout (FR-5): snapshot order rows, clear the cart.

    Refuses an empty cart. ``orders.status`` is ``demo_paid`` — no payment
    involved anywhere in the flow. ``order_items`` freeze ``name_snapshot``
    and ``unit_price`` from the catalog at purchase time, so later catalog
    edits never rewrite order history (3.8 snapshot semantics).
    """
    lines = await _cart_lines(db, user_id)
    if not lines:
        raise CartError("empty_cart", "The cart is empty; nothing to check out.")

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    total = 0.0
    items_payload: list[dict[str, Any]] = []
    for line in lines:
        product = catalog.get_product(line.product_id)
        unit_price = product.price if product is not None else 0.0
        name_snapshot = product.name_en if product is not None else line.product_id
        line_total = round(unit_price * line.quantity, 2)
        total += line_total
        db.add(
            OrderItem(
                order_id=order_id,
                product_id=line.product_id,
                name_snapshot=name_snapshot,
                unit_price=unit_price,
                quantity=line.quantity,
                variant=dict(line.variant) if line.variant else None,
            )
        )
        items_payload.append(
            {
                "product_id": line.product_id,
                "name": name_snapshot,
                "unit_price": unit_price,
                "quantity": line.quantity,
                "variant": dict(line.variant) if line.variant else None,
                "line_total": line_total,
            }
        )
        await record_interaction(
            db,
            user_id,
            type="purchase",
            product_id=line.product_id,
            session_id=session_id,
            order_id=order_id,
            quantity=line.quantity,
        )
    total = round(total, 2)
    db.add(
        Order(id=order_id, user_id=user_id, total=total, currency=CURRENCY, status="demo_paid")
    )
    for line in lines:
        await db.delete(line)
    cart = await db.get(Cart, user_id)
    if cart is not None:
        cart.updated_at = datetime.now(UTC)
    await db.flush()
    return {
        "order_id": order_id,
        "status": "demo_paid",
        "currency": CURRENCY,
        "total": total,
        "items": items_payload,
        "demo": True,
        "shipping_note": shipping_note,
    }
