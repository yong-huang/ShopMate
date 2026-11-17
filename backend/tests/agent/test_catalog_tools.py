"""Catalog tools (System Design 3.3): search/details/compare over the real index.

Seam: ``app.agent.catalog_tools`` — the three LLM-callable tools T05 wires to
the T02 retrieval index and the catalog adapter. Tests run against the
committed ``products.json`` (150 deterministic records), so every expected id,
price, and rating is a known-good literal from the catalog itself.
"""

import json

from app.agent.catalog_tools import (
    COMPARE_PRODUCTS_TOOL,
    GET_PRODUCT_DETAILS_TOOL,
    SEARCH_PRODUCTS_TOOL,
    MAX_SEARCH_LIMIT,
    register_catalog_tools,
)
from app.agent.tools import ToolRegistry, tool_registry
from app.catalog.adapter import JsonCatalogAdapter
from app.retrieval.index import RetrievalIndex

ADAPTER = JsonCatalogAdapter()


async def make_registry() -> ToolRegistry:
    """The default registry with the catalog tools over a keyword-only index."""
    index = await RetrievalIndex.build(ADAPTER)
    registry = tool_registry()
    register_catalog_tools(registry, ADAPTER, index)
    return registry


# --- Specs (OpenAI function schemas per 3.3) -------------------------------


def test_search_products_spec_declares_query_filters_and_limit_max_eight():
    wire = SEARCH_PRODUCTS_TOOL.to_wire()
    assert wire["function"]["name"] == "search_products"
    parameters = wire["function"]["parameters"]
    assert parameters["required"] == ["query"]
    assert parameters["properties"]["filters"]["type"] == "object"
    assert parameters["properties"]["limit"]["maximum"] == MAX_SEARCH_LIMIT == 8


def test_get_product_details_spec_requires_product_id():
    wire = GET_PRODUCT_DETAILS_TOOL.to_wire()
    assert wire["function"]["name"] == "get_product_details"
    assert wire["function"]["parameters"]["required"] == ["product_id"]


def test_compare_products_spec_requires_two_to_three_ids():
    wire = COMPARE_PRODUCTS_TOOL.to_wire()
    assert wire["function"]["name"] == "compare_products"
    parameters = wire["function"]["parameters"]
    assert parameters["required"] == ["product_ids"]
    ids = parameters["properties"]["product_ids"]
    assert ids["minItems"] == 2 and ids["maxItems"] == 3


def test_registered_registry_advertises_all_three_catalog_tools():
    registry = ToolRegistry()
    register_catalog_tools(registry, ADAPTER, None)
    names = [spec.name for spec in registry.specs()]
    assert names == ["search_products", "get_product_details", "compare_products"]


# --- search_products --------------------------------------------------------


async def test_search_returns_bilingual_summaries_with_match_hints():
    registry = await make_registry()
    output = await registry.execute(
        "search_products", {"query": "Aurora X9 headphones", "limit": 3}
    )
    assert output.ok is True
    results = json.loads(output.content)
    assert 1 <= len(results) <= 3
    top = results[0]
    assert top["id"] == "ELE-001"
    assert top["name_en"] == "Aurora X9 Wireless Noise-Cancelling Headphones"
    assert top["name_zh"] == "极光 X9 无线降噪耳机"
    assert top["price"] == 249.99
    assert top["currency"] == "USD"
    assert top["rating"] == 4.7
    assert top["match_hint"]


async def test_search_attaches_validated_card_payloads_at_execution_time():
    registry = await make_registry()
    output = await registry.execute(
        "search_products", {"query": "Aurora X9 headphones", "limit": 2}
    )
    assert output.cards, "tool must attach pending cards at execution time"
    for card in output.cards:
        assert card["image_url"] == f"/static/catalog/images/{card['product_id']}.svg"
        assert card["price"] > 0 and 0 < card["rating"] <= 5
        assert card["name_zh"] and card["name_en"]
    ids = {card["product_id"] for card in output.cards}
    assert "ELE-001" in ids


async def test_search_applies_category_price_and_tag_filters():
    registry = await make_registry()
    output = await registry.execute(
        "search_products",
        {
            "query": "waterproof",
            "filters": {
                "category": "Apparel & Accessories",
                "price_min": 100,
                "price_max": 250,
                "tags": ["waterproof"],
            },
            "limit": 5,
        },
    )
    results = json.loads(output.content)
    assert results, "APP-001 (rain jacket, $189) must match these filters"
    for result in results:
        assert result["category"] == "Apparel & Accessories"
        assert 100 <= result["price"] <= 250
    assert any(result["id"] == "APP-001" for result in results)


async def test_search_limit_is_clamped_to_eight():
    registry = await make_registry()
    output = await registry.execute(
        "search_products", {"query": "stainless steel bottle kitchen", "limit": 50}
    )
    results = json.loads(output.content)
    assert len(results) <= MAX_SEARCH_LIMIT


async def test_search_with_empty_query_fails_honestly():
    registry = await make_registry()
    output = await registry.execute("search_products", {"query": "   "})
    assert output.ok is False
    assert output.cards == []
    assert "query" in output.content.lower()


# --- get_product_details ----------------------------------------------------


async def test_details_returns_full_record_including_reviews_and_sizing():
    registry = await make_registry()
    output = await registry.execute(
        "get_product_details", {"product_id": "APP-001"}
    )
    assert output.ok is True
    record = json.loads(output.content)
    assert record["id"] == "APP-001"
    assert record["price"] == 189.0
    assert record["specs"] and record["reviews"]
    assert record["sizing"]["sizes"] == ["XS", "S", "M", "L", "XL", "XXL"]
    assert record["stock"] >= 0
    # Full fields the storefront record defines — the model may only state
    # what is present here (PRD FR-2).
    assert "materials" in record and "care" in record


async def test_details_attaches_ground_truth_card():
    registry = await make_registry()
    output = await registry.execute(
        "get_product_details", {"product_id": "ELE-001"}
    )
    assert output.cards == [
        {
            "product_id": "ELE-001",
            "name_en": "Aurora X9 Wireless Noise-Cancelling Headphones",
            "name_zh": "极光 X9 无线降噪耳机",
            "price": 249.99,
            "currency": "USD",
            "rating": 4.7,
            "image_url": "/static/catalog/images/ELE-001.svg",
            "match_hint": "",
        }
    ]


async def test_details_unknown_product_fails_honestly():
    registry = await make_registry()
    output = await registry.execute(
        "get_product_details", {"product_id": "FAKE-999"}
    )
    assert output.ok is False
    assert "FAKE-999" in output.content
    assert output.cards == []


# --- compare_products -------------------------------------------------------


async def test_compare_builds_normalized_matrix_and_strengths():
    registry = await make_registry()
    output = await registry.execute(
        "compare_products", {"product_ids": ["ELE-001", "ELE-002"]}
    )
    assert output.ok is True
    comparison = output.comparison
    assert comparison is not None
    assert comparison["product_ids"] == ["ELE-001", "ELE-002"]

    matrix = comparison["matrix"]
    assert matrix["columns"] == ["ELE-001", "ELE-002"]
    labels = {row["label"] for row in matrix["rows"]}
    assert {"Price", "Rating"} <= labels
    by_label = {row["label"]: row["values"] for row in matrix["rows"]}
    assert by_label["Price"]["ELE-001"] == "$249.99"
    assert by_label["Rating"]["ELE-002"] == f"{ADAPTER.get_product('ELE-002').rating} / 5"

    strengths = comparison["strengths"]
    assert [entry["product_id"] for entry in strengths] == ["ELE-001", "ELE-002"]
    assert all(entry["points"] for entry in strengths)

    # The model sees the same matrix + strengths as JSON content.
    content = json.loads(output.content)
    assert content["matrix"] == matrix
    assert content["strengths"] == strengths


async def test_compare_rejects_fewer_than_two_or_more_than_three_ids():
    registry = await make_registry()
    for bad in (["ELE-001"], ["ELE-001", "ELE-002", "ELE-003", "ELE-004"]):
        output = await registry.execute("compare_products", {"product_ids": bad})
        assert output.ok is False
        assert output.comparison is None


async def test_compare_unknown_or_duplicate_ids_fail_honestly():
    registry = await make_registry()
    output = await registry.execute(
        "compare_products", {"product_ids": ["ELE-001", "FAKE-999"]}
    )
    assert output.ok is False
    assert "FAKE-999" in output.content

    duplicate = await registry.execute(
        "compare_products", {"product_ids": ["ELE-001", "ELE-001"]}
    )
    assert duplicate.ok is False
    assert output.comparison is None
