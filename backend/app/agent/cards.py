"""Product cards: SSE payload schema, block parsing, validation (3.5/3.7).

Product cards are the only rendering path for products (System Design 3.5):

- :class:`ProductCardPayload` is the exact §3.7 ``product_card`` SSE schema;
  payloads are always built from catalog records (:func:`card_from_product`),
  never from model prose.
- Maya references products in her reply with ``product-card`` fenced JSON
  blocks (``product_id`` + claimed ``price``/``rating`` from tool output);
  :func:`parse_card_blocks` extracts those claims.
- :func:`validate_card` gates each claim against the real catalog: the id
  must exist, the claimed price/rating must equal catalog values, and the
  payload shape must match the schema. Any violation names a reason so the
  agent graph can retry once, then strip (3.5 failure policy).

The module also hosts the two small text helpers shared by the chat endpoint
and the eval runner: :func:`strip_card_blocks` and the en/zh
:func:`detect_language` heuristic — one definition so production and eval
cannot drift.
"""

import json
import re
from dataclasses import dataclass
from math import isclose
from typing import Any

from pydantic import BaseModel, Field

from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductSummary

# Price/rating equality tolerance: JSON round-trips of catalog floats must
# compare equal even after the model copies them through its own text.
CLAIM_TOLERANCE = 1e-9

CARD_BLOCK = re.compile(
    r"```product-card\s*\n(?P<body>.*?)\n?\s*```", re.DOTALL
)

CJK_RATIO_ZH_THRESHOLD = 0.2  # zh at ≥ 0.2 CJK character share

CLAIM_KEYS = {"product_id", "price", "rating"}


class ProductCardPayload(BaseModel):
    """The §3.7 ``product_card`` SSE event payload (validated only)."""

    product_id: str
    name_en: str
    name_zh: str
    price: float = Field(gt=0)
    currency: str
    rating: float = Field(gt=0, le=5)
    image_url: str
    match_hint: str


def card_image_url(product_id: str) -> str:
    """Card images are the static catalog SVGs mounted by the backend."""
    return f"/static/catalog/images/{product_id}.svg"


def strip_card_blocks(text: str) -> str:
    """The reply as the shopper sees it: ``product-card`` fences removed.

    The frontend strips card blocks before display, so language detection
    must not let ASCII card JSON dilute the CJK ratio of a Chinese reply.
    """
    return CARD_BLOCK.sub("", text)


def detect_language(text: str) -> str | None:
    """The production en/zh heuristic: zh at ≥ 0.2 CJK share, else en.

    Shared by the chat endpoint (``sessions.lang``) and the eval runner's
    ``language`` check so the two can never disagree. ``None`` when there is
    no text to classify.
    """
    stripped = strip_card_blocks(text).strip()
    if not stripped:
        return None
    cjk = sum(1 for char in stripped if "\u4e00" <= char <= "\u9fff")
    return "zh" if cjk / len(stripped) >= CJK_RATIO_ZH_THRESHOLD else "en"


def card_from_product(product: ProductSummary, match_hint: str = "") -> ProductCardPayload:
    """Build a card payload from a catalog record (ground truth, never prose)."""
    return ProductCardPayload(
        product_id=product.id,
        name_en=product.name_en,
        name_zh=product.name_zh,
        price=product.price,
        currency=product.currency,
        rating=product.rating,
        image_url=card_image_url(product.id),
        match_hint=match_hint,
    )


def parse_card_blocks(text: str) -> list[dict[str, Any]]:
    """Extract ``product-card`` fenced JSON blocks from a model reply.

    Non-JSON bodies and non-object values are ignored — they become ordinary
    text the frontend strips from display; they never become cards.
    """
    claims: list[dict[str, Any]] = []
    for match in CARD_BLOCK.finditer(text):
        try:
            value = json.loads(match.group("body"))
        except ValueError:
            continue
        if isinstance(value, dict):
            claims.append(value)
    return claims


@dataclass
class CardIssue:
    """Why one card claim failed validation (3.5) — for retry and audit."""

    product_id: str
    reason: str  # unknown_product | price_mismatch | rating_mismatch | schema
    detail: str


def _schema_issue(detail: str) -> CardIssue:
    return CardIssue(product_id="<unknown>", reason="schema", detail=detail)


def validate_card(claim: Any, adapter: CatalogAdapter) -> CardIssue | None:
    """Validate one card claim against the catalog (System Design 3.5).

    Checks, in order: (c) payload shape matches the SSE card claim schema;
    (a) the product id exists in the catalog; (b) claimed price and rating
    equal catalog values. ``None`` means the claim is truthful.
    """
    if not isinstance(claim, dict):
        return _schema_issue(f"card claim must be a JSON object, got {type(claim).__name__}")
    if set(claim.keys()) != CLAIM_KEYS:
        extra = sorted(set(claim.keys()) - CLAIM_KEYS)
        missing = sorted(CLAIM_KEYS - set(claim.keys()))
        return _schema_issue(
            f"card claim fields must be exactly {sorted(CLAIM_KEYS)}"
            f" (missing: {missing}, unexpected: {extra})"
        )
    product_id = claim["product_id"]
    price = claim["price"]
    rating = claim["rating"]
    if not isinstance(product_id, str) or not product_id:
        return _schema_issue("product_id must be a non-empty string")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return _schema_issue("price must be a number")
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        return _schema_issue("rating must be a number")

    product = adapter.get_product(product_id)
    if product is None:
        return CardIssue(
            product_id=product_id,
            reason="unknown_product",
            detail=f"product '{product_id}' does not exist in the catalog",
        )
    if not isclose(float(price), product.price, rel_tol=CLAIM_TOLERANCE, abs_tol=CLAIM_TOLERANCE):
        return CardIssue(
            product_id=product_id,
            reason="price_mismatch",
            detail=(
                f"claimed price {price} for '{product_id}' but catalog says {product.price}"
            ),
        )
    if not isclose(float(rating), product.rating, rel_tol=CLAIM_TOLERANCE, abs_tol=CLAIM_TOLERANCE):
        return CardIssue(
            product_id=product_id,
            reason="rating_mismatch",
            detail=(
                f"claimed rating {rating} for '{product_id}' but catalog says {product.rating}"
            ),
        )
    return None
