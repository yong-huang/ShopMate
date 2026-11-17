"""Cart + checkout tools on the ToolRegistry (System Design 3.3, PRD FR-4/FR-5).

Four LLM-callable tools — ``add_to_cart`` / ``get_cart`` /
``remove_from_cart`` / ``checkout`` — executed against the *shared* cart
service (``app.cart.service``), so chat and the storefront always agree on
cart state. Each executor opens its own session on the app's SQLite
sessionmaker (catalog tools hit the in-memory index; cart tools hit SQLite
per 3.3), commits, and returns an honest ``(content, ok)`` pair plus the
fresh ``cart_summary`` payload that the SSE layer turns into a
``cart_update`` event (3.7).

The ``context`` argument carries per-turn identity the model never supplies:
``user_id`` (the anonymous cookie id) and ``session_id`` (the chat thread) —
without it the cart tools fail honestly instead of guessing a shopper.
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.agent.tools import ToolOutput, ToolRegistry
from app.cart.service import CartError, add_item, cart_summary, checkout, remove_item
from app.catalog.adapter import CatalogAdapter
from app.gateway.tools import ToolSpec

_ADD_TO_CART_TOOL_SPEC = ToolSpec(
    name="add_to_cart",
    description="Add a catalog product to the shopper's cart, optionally with "
    "quantity and variant (size and/or color — only values the catalog "
    "defines). Re-adding the same product with the same variant merges into "
    "one cart line; the cart never holds duplicates. Returns the cart summary.",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Catalog product id."},
            "quantity": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
                "description": "How many units (demo-stock bounded).",
            },
            "variant": {
                "type": "object",
                "description": "Optional variant, e.g. {\"size\": \"M\"} or "
                "{\"color\": \"Midnight Black\"}.",
                "properties": {
                    "size": {"type": "string", "description": "Catalog-defined size."},
                    "color": {"type": "string", "description": "Catalog-defined colour."},
                },
            },
        },
        "required": ["product_id"],
    },
)

_GET_CART_TOOL_SPEC = ToolSpec(
    name="get_cart",
    description="Return the shopper's current cart: items (product, quantity, "
    "variant, line price) and the cart total.",
    parameters={"type": "object", "properties": {}, "required": []},
)

_REMOVE_FROM_CART_TOOL_SPEC = ToolSpec(
    name="remove_from_cart",
    description="Remove one line from the shopper's cart by its cart_item_id "
    "(get get_cart first to find the id). Returns the cart summary.",
    parameters={
        "type": "object",
        "properties": {
            "cart_item_id": {
                "type": "integer",
                "description": "The cart line id to remove (from get_cart).",
            },
        },
        "required": ["cart_item_id"],
    },
)

_CHECKOUT_TOOL_SPEC = ToolSpec(
    name="checkout",
    description="Place the demo order for the shopper's current cart: writes "
    "the order, clears the cart, and returns the order id. Refuses an empty "
    "cart. Demo only — no payment is processed.",
    parameters={
        "type": "object",
        "properties": {
            "shipping_note": {
                "type": "string",
                "description": "Optional note stored with the demo order.",
            },
        },
        "required": [],
    },
)

# Re-exported for schema tests (the §3.3 contract).
ADD_TO_CART_TOOL = _ADD_TO_CART_TOOL_SPEC
GET_CART_TOOL = _GET_CART_TOOL_SPEC
REMOVE_FROM_CART_TOOL = _REMOVE_FROM_CART_TOOL_SPEC
CHECKOUT_TOOL = _CHECKOUT_TOOL_SPEC

CART_TOOL_NAMES = (
    "add_to_cart",
    "get_cart",
    "remove_from_cart",
    "checkout",
)


class CartTools:
    """Executors for the four cart tools over one sessionmaker + catalog."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        catalog: CatalogAdapter,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._catalog = catalog

    def _identity(self, context: dict[str, Any] | None) -> tuple[str, str | None]:
        """(user_id, session_id) from the turn context; empty cart id fails."""
        if not context or not context.get("user_id"):
            raise CartError(
                "no_shopper",
                "no shopper identity is available in this environment, so the "
                "cart cannot be used.",
            )
        return str(context["user_id"]), context.get("session_id")

    async def add_to_cart(
        self, arguments: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolOutput:
        try:
            user_id, session_id = self._identity(context)
            async with self._sessionmaker() as db:
                summary = await add_item(
                    db,
                    self._catalog,
                    user_id,
                    product_id=arguments.get("product_id"),
                    quantity=arguments.get("quantity", 1),
                    variant=arguments.get("variant"),
                    session_id=session_id,
                )
                await db.commit()
        except CartError as exc:
            return _honest_cart_error(exc)
        return ToolOutput(
            content=json.dumps(summary, ensure_ascii=False, indent=1),
            ok=True,
            cart_summary=summary,
        )

    async def get_cart(
        self, arguments: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolOutput:
        user_id, _ = self._identity(context)
        async with self._sessionmaker() as db:
            summary = await cart_summary(db, self._catalog, user_id)
        return ToolOutput(
            content=json.dumps(summary, ensure_ascii=False, indent=1),
            ok=True,
            cart_summary=summary,
        )

    async def remove_from_cart(
        self, arguments: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolOutput:
        user_id, _ = self._identity(context)
        cart_item_id = arguments.get("cart_item_id")
        if not isinstance(cart_item_id, int) or isinstance(cart_item_id, bool):
            return ToolOutput(
                content="Error: 'cart_item_id' must be the integer id of a cart "
                "line (call get_cart to list them).",
                ok=False,
            )
        async with self._sessionmaker() as db:
            try:
                summary = await remove_item(db, self._catalog, user_id, cart_item_id)
                await db.commit()
            except CartError as exc:
                return _honest_cart_error(exc)
            return ToolOutput(
                content=json.dumps(summary, ensure_ascii=False, indent=1),
                ok=True,
                cart_summary=summary,
            )

    async def checkout(
        self, arguments: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolOutput:
        user_id, session_id = self._identity(context)
        async with self._sessionmaker() as db:
            try:
                order = await checkout(
                    db,
                    self._catalog,
                    user_id,
                    session_id=session_id,
                    shipping_note=arguments.get("shipping_note"),
                )
                await db.commit()
            except CartError as exc:
                return _honest_cart_error(exc)
        summary = {
            "items": [],
            "item_count": 0,
            "total": 0.0,
            "currency": order["currency"],
        }
        return ToolOutput(
            content=json.dumps(order, ensure_ascii=False, indent=1),
            ok=True,
            cart_summary=summary,
        )


def _honest_cart_error(exc: CartError) -> ToolOutput:
    """Cart validation failures become visible, honest tool errors (3.3).

    The raw message (with the offending id / valid options) is passed through
    so the model can self-correct instead of guessing.
    """
    return ToolOutput(content=f"Error: {exc.message}", ok=False)


def register_cart_tools(
    registry: ToolRegistry,
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: CatalogAdapter,
) -> CartTools:
    """Register the four cart tools; returns the handle (for tests)."""
    tools = CartTools(sessionmaker, catalog)
    registry.register(_ADD_TO_CART_TOOL_SPEC, tools.add_to_cart)
    registry.register(_GET_CART_TOOL_SPEC, tools.get_cart)
    registry.register(_REMOVE_FROM_CART_TOOL_SPEC, tools.remove_from_cart)
    registry.register(_CHECKOUT_TOOL_SPEC, tools.checkout)
    return tools
