"""Pydantic models for catalog data (System Design 3.8).

These models define the adapter's public contract. Bilingual display
fields (``name_*`` / ``description_*``) are required per PRD FR-7;
``sizing`` / ``care`` / ``materials`` appear on apparel records.
"""

from typing import Any

from pydantic import BaseModel, Field


class Review(BaseModel):
    """A shopper review; may be English-only per FR-7."""

    author: str
    rating: int = Field(ge=1, le=5)
    comment: str


class ProductSummary(BaseModel):
    """Storefront list/card fields — also the retrieval result shape."""

    id: str
    slug: str
    name_en: str
    name_zh: str
    category: str
    price: float = Field(gt=0)
    currency: str = "USD"
    rating: float = Field(gt=0, le=5)
    image: str
    tags: list[str]
    featured: bool = False


class ProductDetail(ProductSummary):
    """Full product record backing the detail page and chat tools."""

    description_en: str
    description_zh: str
    attributes: dict[str, Any]
    specs: list[dict[str, str]]
    reviews: list[Review]
    stock: int = Field(ge=0)
    sizing: dict[str, Any] | None = None
    care: str | None = None
    materials: dict[str, Any] | None = None


class CategorySummary(BaseModel):
    """One storefront category and its product count."""

    name: str
    count: int
