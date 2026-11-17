"""CatalogAdapter interface + JSON mock implementation (ADR-008).

``CatalogAdapter`` is the single seam between ShopMate and whatever
commerce backend serves products. The demo ships ``JsonCatalogAdapter``
over ``backend/data/catalog/products.json``; a real-platform adapter
(Shopify / custom commerce API, see the PRD v2 roadmap) would implement
the same four methods without touching agent or API code.
"""

import json
from pathlib import Path
from typing import Protocol

from app.catalog.schemas import (
    CategorySummary,
    ProductDetail,
    ProductSummary,
)

SortOrder = str  # "featured" | "price_asc" | "price_desc" | "rating"

SORT_ORDERS: tuple[str, ...] = ("featured", "price_asc", "price_desc", "rating")

# Keyword search spans these product fields (case-insensitive substring).
_SEARCHABLE_FIELDS = ("name_en", "name_zh", "category", "tags", "attributes")


def default_catalog_dir() -> Path:
    """backend/data/catalog, resolved from this file's location."""
    return Path(__file__).resolve().parents[2] / "data" / "catalog"


class CatalogAdapter(Protocol):
    """The commerce data seam every catalog backend must implement."""

    def list_products(
        self,
        *,
        category: str | None = None,
        q: str | None = None,
        sort: SortOrder = "featured",
        limit: int | None = None,
    ) -> list[ProductDetail]:
        """List products with optional category/keyword filters and sort."""
        ...

    def get_product(self, product_id: str) -> ProductDetail | None:
        """Return one product by id, or None when it does not exist."""
        ...

    def categories(self) -> list[CategorySummary]:
        """All storefront categories with product counts."""
        ...

    def featured(self) -> list[ProductSummary]:
        """The curated/featured products for the storefront home page."""
        ...


class JsonCatalogAdapter:
    """Mock catalog implementation over the generated products.json."""

    def __init__(self, catalog_dir: Path | None = None) -> None:
        self._catalog_dir = catalog_dir if catalog_dir is not None else default_catalog_dir()
        raw = json.loads(
            (self._catalog_dir / "products.json").read_text(encoding="utf-8")
        )
        self._products: dict[str, ProductDetail] = {
            product.id: product for product in
            (ProductDetail.model_validate(item) for item in raw)
        }

    def list_products(
        self,
        *,
        category: str | None = None,
        q: str | None = None,
        sort: SortOrder = "featured",
        limit: int | None = None,
    ) -> list[ProductDetail]:
        """List products with optional category/keyword filters and sort."""
        if sort not in SORT_ORDERS:
            raise ValueError(
                f"Unknown sort {sort!r}; expected one of {SORT_ORDERS}"
            )
        products = list(self._products.values())
        if category is not None:
            wanted = category.casefold()
            products = [
                product for product in products
                if product.category.casefold() == wanted
            ]
        if q:
            needle = q.casefold()
            products = [
                product for product in products
                if _matches(product, needle)
            ]
        products.sort(key=_sort_key(sort))
        if limit is not None:
            products = products[:limit]
        return products

    def get_product(self, product_id: str) -> ProductDetail | None:
        """Return one product by id, or None when it does not exist."""
        return self._products.get(product_id)

    def categories(self) -> list[CategorySummary]:
        """All storefront categories with product counts."""
        counts: dict[str, int] = {}
        for product in self._products.values():
            counts[product.category] = counts.get(product.category, 0) + 1
        return [
            CategorySummary(name=name, count=count)
            for name, count in sorted(counts.items())
        ]

    def featured(self) -> list[ProductSummary]:
        """The curated/featured products for the storefront home page."""
        return [
            ProductSummary.model_validate(product.model_dump())
            for product in self._products.values()
            if product.featured
        ]


def _matches(product: ProductDetail, needle: str) -> bool:
    """Case-insensitive substring match across the searchable fields."""
    if needle in product.name_en.casefold():
        return True
    if needle in product.name_zh.casefold():
        return True
    if needle in product.category.casefold():
        return True
    if any(needle in tag.casefold() for tag in product.tags):
        return True
    return any(needle in str(value).casefold()
               for value in product.attributes.values())


def _sort_key(sort: SortOrder):
    """Build a stable sort key: featured-first, then category ordering."""
    if sort == "price_asc":
        return lambda product: (product.price, product.id)
    if sort == "price_desc":
        return lambda product: (-product.price, product.id)
    if sort == "rating":
        return lambda product: (-product.rating, product.id)
    return lambda product: (not product.featured, product.id)
