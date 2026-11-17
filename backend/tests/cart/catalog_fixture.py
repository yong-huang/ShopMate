"""Shared hermetic catalog fixture for cart tests (System Design 3.8).

A tiny two-product catalog written to a temp dir and loaded through the real
``JsonCatalogAdapter``, so cart tests exercise the committed adapter seam
while every price/size assertion stays an independent known literal:

- ``TST-001`` apparel, $50.00, sizes S/M/L, colour "Moss Green", stock 10
- ``TST-002`` electronics, $249.99, no sizes, colour "Midnight Black", stock 2
"""

import json
from pathlib import Path

from app.catalog.adapter import JsonCatalogAdapter

PRICE_APPAREL = 50.0
PRICE_HEADPHONES = 249.99

_BASE_FIELDS = {
    "currency": "USD",
    "rating": 4.5,
    "tags": ["test"],
    "featured": False,
    "description_en": "A test product.",
    "description_zh": "测试商品。",
    "specs": [],
    "reviews": [],
}


def _product(**overrides) -> dict:
    fields = {
        **_BASE_FIELDS,
        "id": "TST-000",
        "slug": "test-product",
        "name_en": "Test Product",
        "name_zh": "测试商品",
        "category": "Electronics",
        "price": 10.0,
        "image": "test.svg",
        "attributes": {"colour": "Midnight Black"},
        "stock": 5,
    }
    fields.update(overrides)
    return fields


def write_catalog(catalog_dir: Path, products: list[dict]) -> None:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "products.json").write_text(
        json.dumps(products, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def default_products() -> list[dict]:
    """The two hermetic products shared by the cart tests."""
    return [
        _product(
            id="TST-001",
            slug="trail-jacket",
            name_en="Trailblazer Rain Jacket",
            name_zh="拓路者雨衣",
            category="Apparel & Accessories",
            price=PRICE_APPAREL,
            attributes={"colour": "Moss Green"},
            stock=10,
            sizing={"sizes": ["S", "M", "L"], "fit": "Regular"},
            care="Machine wash cold.",
            materials={"shell": "Recycled nylon"},
        ),
        _product(
            id="TST-002",
            slug="aurora-headphones",
            name_en="Aurora X9 Headphones",
            name_zh="极光 X9 耳机",
            category="Electronics",
            price=PRICE_HEADPHONES,
            attributes={"colour": "Midnight Black"},
            stock=2,
        ),
    ]


def catalog_adapter(catalog_dir: Path) -> JsonCatalogAdapter:
    """Adapter over the fixture catalog (and the seam to reload after edits)."""
    return JsonCatalogAdapter(catalog_dir=catalog_dir)
