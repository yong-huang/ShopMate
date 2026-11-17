"""Catalog tools (System Design 3.3): search, details, compare.

The three LLM-callable tools T05 wires onto the ``ToolRegistry``. Each
executor returns honest ``(content, ok)`` text for the model *and* attaches
structured payloads at tool-execution time (``ToolOutput.cards`` /
``ToolOutput.comparison``) built from the catalog records the tool actually
read — the guardrail (3.5) and the SSE card events (3.7) consume those, so
the user never sees a product the tools did not return.

All calls hit the in-process retrieval index + catalog adapter (no network
beyond embeddings at index build), so behavior is deterministic.
"""

import json
from typing import Any

from app.agent.cards import card_from_product
from app.agent.tools import ToolOutput, ToolRegistry
from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductDetail
from app.gateway.tools import ToolSpec
from app.retrieval.index import RetrievalIndex

MAX_SEARCH_LIMIT = 8  # 3.3: limit <= 8 per search call

_SEARCH_PITCH_CHARS = 180

SEARCH_PRODUCTS_TOOL = ToolSpec(
    name="search_products",
    description="Hybrid search over the ShopMate catalog. Returns ranked "
    "product summaries (bilingual names, price, rating, match hint). Use for "
    "guided discovery before recommending; recommend only what it returns.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text query describing what the shopper wants.",
            },
            "filters": {
                "type": "object",
                "description": "Optional structured filters, all optional.",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Exact category: Electronics, Home & Kitchen, "
                        "Apparel & Accessories, or Sports & Outdoors.",
                    },
                    "price_min": {"type": "number"},
                    "price_max": {"type": "number"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "All tags must match (AND).",
                    },
                    "attributes": {
                        "type": "object",
                        "description": "Exact attribute values, e.g. {\"brand\": \"Aurora\"}.",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_SEARCH_LIMIT,
                "default": 5,
                "description": f"Max results (<= {MAX_SEARCH_LIMIT}).",
            },
        },
        "required": ["query"],
    },
)

GET_PRODUCT_DETAILS_TOOL = ToolSpec(
    name="get_product_details",
    description="Full catalog record for one product: description, specs, "
    "materials, sizing, care, reviews, stock. Call before answering detailed "
    "questions about a specific product; state only what the record contains.",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Catalog product id."},
        },
        "required": ["product_id"],
    },
)

COMPARE_PRODUCTS_TOOL = ToolSpec(
    name="compare_products",
    description="Compare 2-3 catalog products: normalized attribute matrix "
    "plus per-product strengths. Use before advising between candidates.",
    parameters={
        "type": "object",
        "properties": {
            "product_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 3,
                "description": "Two or three catalog product ids.",
            },
        },
        "required": ["product_ids"],
    },
)


def _honest_error(message: str) -> ToolOutput:
    return ToolOutput(content=f"Error: {message}", ok=False)


def _pitch(product: ProductDetail) -> str:
    """One-line English pitch (first sentence, capped) for search summaries."""
    sentence = product.description_en.split(". ")[0].strip()
    if len(sentence) > _SEARCH_PITCH_CHARS:
        sentence = sentence[: _SEARCH_PITCH_CHARS - 1].rstrip() + "…"
    return sentence


def _clamp_limit(value: Any) -> int:
    """Clamp the model-provided limit into [1, MAX_SEARCH_LIMIT]."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 5
    return max(1, min(MAX_SEARCH_LIMIT, limit))


class CatalogTools:
    """Executors for the three catalog tools over one adapter + index."""

    def __init__(self, adapter: CatalogAdapter, index: RetrievalIndex | None = None) -> None:
        self._adapter = adapter
        self._index = index

    def set_index(self, index: RetrievalIndex) -> None:
        """Attach the retrieval index once it is built (app lifespan)."""
        self._index = index

    async def search_products(self, arguments: dict[str, Any]) -> ToolOutput:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return _honest_error("'query' is required and must be non-empty.")
        if self._index is None:
            return _honest_error("the catalog search index is not ready yet.")

        filters = arguments.get("filters") or {}
        if not isinstance(filters, dict):
            return _honest_error("'filters' must be an object.")
        hits = await self._index.search(
            query,
            filters.get("category"),
            _clamp_limit(arguments.get("limit")),
            price_min=filters.get("price_min"),
            price_max=filters.get("price_max"),
            tags=filters.get("tags"),
            attributes=filters.get("attributes"),
        )
        if not hits:
            return ToolOutput(
                content=f"No catalog products matched the query {query!r}. "
                "Tell the shopper honestly and offer to broaden the search.",
                ok=True,
            )
        summaries = []
        cards = []
        for hit in hits:
            full = self._adapter.get_product(hit.product.id) or hit.product
            summaries.append(
                {
                    "id": hit.product.id,
                    "name_en": hit.product.name_en,
                    "name_zh": hit.product.name_zh,
                    "category": hit.product.category,
                    "price": hit.product.price,
                    "currency": hit.product.currency,
                    "rating": hit.product.rating,
                    "pitch": _pitch(full),
                    "match_hint": hit.match_hint,
                }
            )
            cards.append(card_from_product(hit.product, hit.match_hint).model_dump())
        return ToolOutput(
            content=json.dumps(summaries, ensure_ascii=False, indent=1),
            ok=True,
            cards=cards,
        )

    async def get_product_details(self, arguments: dict[str, Any]) -> ToolOutput:
        product_id = arguments.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            return _honest_error("'product_id' is required.")
        product = self._adapter.get_product(product_id)
        if product is None:
            return _honest_error(
                f"product '{product_id}' does not exist in the catalog. "
                "Never invent details for it; search_products can list real ids."
            )
        card = card_from_product(product).model_dump()
        return ToolOutput(
            content=json.dumps(product.model_dump(), ensure_ascii=False, indent=1),
            ok=True,
            cards=[card],
        )

    async def compare_products(self, arguments: dict[str, Any]) -> ToolOutput:
        ids = arguments.get("product_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return _honest_error("'product_ids' must be a list of product id strings.")
        if not 2 <= len(ids) <= 3:
            return _honest_error("compare exactly 2 or 3 products.")
        if len(set(ids)) != len(ids):
            return _honest_error("product_ids must be distinct.")
        products = [self._adapter.get_product(product_id) for product_id in ids]
        unknown = [i for i, product in zip(ids, products) if product is None]
        if unknown:
            return _honest_error(
                f"products {unknown} do not exist in the catalog; "
                "compare only ids returned by your tools."
            )
        comparison = _build_comparison([p for p in products if p is not None])
        return ToolOutput(
            content=json.dumps(comparison, ensure_ascii=False, indent=1),
            ok=True,
            comparison=comparison,
        )


def _build_comparison(products: list[ProductDetail]) -> dict[str, Any]:
    """Normalized attribute matrix + per-product strengths (3.3)."""
    ids = [product.id for product in products]
    rows: list[dict[str, Any]] = [
        {
            "label": "Price",
            "values": {p.id: f"${p.price:.2f}" for p in products},
        },
        {
            "label": "Rating",
            "values": {p.id: f"{p.rating:g} / 5" for p in products},
        },
        {
            "label": "Category",
            "values": {p.id: p.category for p in products},
        },
    ]
    spec_labels: list[str] = []
    for product in products:
        for spec in product.specs:
            if spec["label"] not in spec_labels:
                spec_labels.append(spec["label"])
    for label in spec_labels:
        values = {
            product.id: next(
                (spec["value"] for spec in product.specs if spec["label"] == label), None
            )
            for product in products
        }
        rows.append({"label": label, "values": values})

    strengths = [
        {"product_id": product.id, "points": _strength_points(product, products)}
        for product in products
    ]
    return {"product_ids": ids, "matrix": {"columns": ids, "rows": rows}, "strengths": strengths}


def _strength_points(product: ProductDetail, products: list[ProductDetail]) -> list[str]:
    """Deterministic per-product strengths from catalog data only."""
    points: list[str] = []
    cheapest = min(p.price for p in products)
    if product.price == cheapest:
        points.append(f"Lowest price of the set (${product.price:.2f})")
    best_rating = max(p.rating for p in products)
    if product.rating == best_rating:
        points.append(f"Highest rating of the set ({product.rating:g} / 5)")
    labels = [spec["label"] for other in products for spec in other.specs]
    for spec in product.specs:
        if labels.count(spec["label"]) == 1:
            points.append(f"{spec['label']}: {spec['value']}")
    if not points:
        top_review = max(product.reviews, key=lambda review: review.rating, default=None)
        if top_review is not None:
            points.append(f"Top review ({top_review.rating}/5): {top_review.comment}")
    return points


def register_catalog_tools(
    registry: ToolRegistry,
    adapter: CatalogAdapter,
    index: RetrievalIndex | None = None,
) -> CatalogTools:
    """Register the three catalog tools; returns the handle for set_index."""
    tools = CatalogTools(adapter, index)
    registry.register(SEARCH_PRODUCTS_TOOL, tools.search_products)
    registry.register(GET_PRODUCT_DETAILS_TOOL, tools.get_product_details)
    registry.register(COMPARE_PRODUCTS_TOOL, tools.compare_products)
    return tools
