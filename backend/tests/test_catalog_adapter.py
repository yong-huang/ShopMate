"""Tests for the CatalogAdapter seam (ticket T02, ADR-008).

The adapter is the single integration point for commerce data: the JSON
mock implementation backs the demo, while future real-platform adapters
implement the same interface. Tests run against the committed catalog in
``backend/data/catalog``.
"""

import pytest

from app.catalog.adapter import JsonCatalogAdapter, default_catalog_dir
from app.catalog.schemas import CategorySummary, ProductDetail

EXPECTED_SPLITS = {
    "Electronics": 30,
    "Home & Kitchen": 40,
    "Apparel & Accessories": 40,
    "Sports & Outdoors": 40,
}


@pytest.fixture(scope="module")
def adapter() -> JsonCatalogAdapter:
    return JsonCatalogAdapter(default_catalog_dir())


def test_adapter_lists_the_full_committed_catalog(adapter: JsonCatalogAdapter) -> None:
    products = adapter.list_products()

    assert len(products) == 150
    assert all(isinstance(product, ProductDetail) for product in products)


def test_get_product_returns_bilingual_detail_by_id(
    adapter: JsonCatalogAdapter,
) -> None:
    product = adapter.get_product("ELE-001")

    assert product is not None
    assert product.name_en == "Aurora X9 Wireless Noise-Cancelling Headphones"
    assert product.name_zh
    assert product.description_zh
    assert len(product.reviews) >= 3
    assert product.image.endswith(".svg")
    assert adapter.get_product("NOPE-999") is None


def test_list_filters_by_category(adapter: JsonCatalogAdapter) -> None:
    electronics = adapter.list_products(category="Electronics")

    assert len(electronics) == EXPECTED_SPLITS["Electronics"]
    assert {p.category for p in electronics} == {"Electronics"}


def test_list_matches_keyword_across_names_tags_and_brands(
    adapter: JsonCatalogAdapter,
) -> None:
    by_english = adapter.list_products(q="noise cancelling")
    assert any(p.id == "ELE-001" for p in by_english)

    by_chinese = adapter.list_products(q="保温杯")
    assert any(p.id == "HOM-006" for p in by_chinese)

    by_brand = adapter.list_products(q="Aurora")
    assert {p.id for p in by_brand} == {"ELE-001"}

    by_tag = adapter.list_products(q="yoga")
    assert all(
        "yoga" in [t.lower() for t in p.tags]
        or "yoga" in p.name_en.lower()
        or "瑜伽" in p.name_zh
        for p in by_tag
    )
    assert len(by_tag) >= 2


def test_list_keyword_is_case_insensitive(adapter: JsonCatalogAdapter) -> None:
    assert {p.id for p in adapter.list_products(q="AURORA")} == {"ELE-001"}
    assert {p.id for p in adapter.list_products(q="aurora")} == {"ELE-001"}


def test_list_sorts_by_price_and_rating(adapter: JsonCatalogAdapter) -> None:
    price_asc = [p.price for p in adapter.list_products(sort="price_asc")]
    assert price_asc == sorted(price_asc)

    price_desc = [p.price for p in adapter.list_products(sort="price_desc")]
    assert price_desc == sorted(price_desc, reverse=True)

    ratings = [p.rating for p in adapter.list_products(sort="rating")]
    assert ratings == sorted(ratings, reverse=True)


def test_default_sort_puts_featured_first_with_stable_ids(
    adapter: JsonCatalogAdapter,
) -> None:
    products = adapter.list_products()

    featured = [p for p in products if p.featured]
    rest = [p for p in products if not p.featured]
    assert featured == products[:30]
    # Within each block the order is deterministic (by id).
    assert [p.id for p in featured] == sorted(p.id for p in featured)
    assert [p.id for p in rest] == sorted(p.id for p in rest)


def test_unknown_sort_is_rejected(adapter: JsonCatalogAdapter) -> None:
    with pytest.raises(ValueError):
        adapter.list_products(sort="cheapest_first")


def test_categories_returns_the_four_groups_with_counts(
    adapter: JsonCatalogAdapter,
) -> None:
    categories = adapter.categories()

    assert isinstance(categories[0], CategorySummary)
    assert {c.name: c.count for c in categories} == EXPECTED_SPLITS


def test_featured_returns_the_thirty_curated_records(
    adapter: JsonCatalogAdapter,
) -> None:
    featured = adapter.featured()

    assert len(featured) == 30
    assert all(p.featured for p in featured)
    assert len({p.category for p in featured}) == 4
