"""Tests for the hybrid RetrievalIndex (ticket T02, System Design 3.4).

The seam is ``RetrievalIndex.build`` + ``search``: structured filters, BM25
scoring, optional vector scoring fused with RRF (k=60), and the silent
keyword-only fallback when the embedding backend is unavailable. Ollama
itself is faked at the ``EmbeddingProvider`` boundary (no network).
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app.catalog.adapter import JsonCatalogAdapter, default_catalog_dir
from app.catalog.schemas import ProductSummary
from app.retrieval.bm25 import KeywordIndex, product_document
from app.retrieval.index import RetrievalIndex
from app.retrieval.vector import ProviderUnavailable

# Fake vector geometry for the fusion-order test on the mini catalog.
# Query "warm jacket" is closest to B, then C, then A; keyword order is
# A, B, C - so the fused order must differ from keyword-only order.
MINI_VECTORS = {
    "warm jacket": [1.0, 0.0],
    "alpine jacket": [0.2, 0.98],   # A: weakest vector match
    "camp blanket": [0.99, 0.14],   # B: strongest vector match
    "desk organizer": [0.6, 0.8],   # C: middle
}


class FakeEmbeddingProvider:
    """Deterministic provider: substring-keyed 2-dim vectors, no I/O."""

    model = "fake-embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            for key, vector in MINI_VECTORS.items():
                if key in lowered:
                    vectors.append(vector)
                    break
            else:
                vectors.append([0.0, 0.0])
        return vectors


class BrokenEmbeddingProvider:
    """Simulates Ollama being down (the fallback path)."""

    model = "broken-embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderUnavailable("ollama is not running")


MINI_PRODUCTS = [
    {
        "id": "A-001", "slug": "alpine-insulated-jacket",
        "name_en": "Alpine Insulated Jacket", "name_zh": "高山保暖夹克",
        "description_en": "A very warm winter jacket.",
        "description_zh": "冬季非常保暖的夹克。",
        "category": "Sports & Outdoors",
        "price": 99.0, "currency": "USD",
        "attributes": {"brand": "Alpine"},
        "specs": [{"label": "Fill", "value": "700 down"}],
        "reviews": [{"author": "Ash", "rating": 5, "comment": "Warm!"}],
        "rating": 5.0, "stock": 10,
        "tags": ["jacket", "warm"], "featured": False,
        "image": "images/A-001.svg",
    },
    {
        "id": "B-001", "slug": "winter-camp-blanket",
        "name_en": "Winter Camp Blanket", "name_zh": "冬季野营毯",
        "description_en": "A warm camp blanket for cold nights outside.",
        "description_zh": "寒冷夜晚户外保暖的野营毯。",
        "category": "Sports & Outdoors",
        "price": 49.0, "currency": "USD",
        "attributes": {"brand": "CampCo"},
        "specs": [{"label": "Material", "value": "fleece"}],
        "reviews": [{"author": "Bo", "rating": 4, "comment": "Cozy."}],
        "rating": 4.0, "stock": 20,
        "tags": ["blanket", "camping"], "featured": False,
        "image": "images/B-001.svg",
    },
    {
        "id": "C-001", "slug": "bamboo-desk-organizer",
        "name_en": "Bamboo Desk Organizer", "name_zh": "竹质桌面收纳架",
        "description_en": "Keep your pens tidy.",
        "description_zh": "让文具井井有条。",
        "category": "Home & Kitchen",
        "price": 24.0, "currency": "USD",
        "attributes": {"brand": "Deskly"},
        "specs": [{"label": "Material", "value": "bamboo"}],
        "reviews": [{"author": "Cy", "rating": 4, "comment": "Tidy."}],
        "rating": 4.0, "stock": 30,
        "tags": ["desk", "organizer"], "featured": False,
        "image": "images/C-001.svg",
    },
]


def mini_adapter(tmp_path: Path) -> JsonCatalogAdapter:
    catalog_dir = tmp_path / "mini-catalog"
    catalog_dir.mkdir()
    (catalog_dir / "products.json").write_text(
        json.dumps(MINI_PRODUCTS), encoding="utf-8"
    )
    return JsonCatalogAdapter(catalog_dir)


@pytest.fixture(scope="module")
def full_adapter() -> JsonCatalogAdapter:
    return JsonCatalogAdapter(default_catalog_dir())


@pytest.mark.asyncio
async def test_search_returns_summaries_with_match_hint(
    full_adapter: JsonCatalogAdapter,
) -> None:
    index = await RetrievalIndex.build(full_adapter)

    hits = await index.search("noise cancelling headphones", limit=5)

    assert len(hits) == 5
    assert hits[0].product.id == "ELE-001"
    assert isinstance(hits[0].product, ProductSummary)
    assert hits[0].product.name_zh
    assert hits[0].product.price > 0
    assert hits[0].match_hint.strip()


@pytest.mark.asyncio
async def test_search_applies_category_filter(
    full_adapter: JsonCatalogAdapter,
) -> None:
    index = await RetrievalIndex.build(full_adapter)

    hits = await index.search(
        "yoga mat", category="Sports & Outdoors", limit=10
    )

    assert hits, "filtered search must return results"
    assert all(
        hit.product.category == "Sports & Outdoors" for hit in hits
    )
    assert hits[0].product.id == "SPO-002"


@pytest.mark.asyncio
async def test_search_handles_chinese_query(
    full_adapter: JsonCatalogAdapter,
) -> None:
    index = await RetrievalIndex.build(full_adapter)

    hits = await index.search("保温杯", limit=3)

    assert hits[0].product.id == "HOM-006"


@pytest.mark.asyncio
async def test_fallback_matches_keyword_only_exactly(
    full_adapter: JsonCatalogAdapter,
) -> None:
    keyword_only = await RetrievalIndex.build(full_adapter, None)
    broken = await RetrievalIndex.build(
        full_adapter, BrokenEmbeddingProvider()
    )

    query = "noise cancelling headphones"
    fallback_hits = await broken.search(query, limit=10)
    expected_ids = [
        hit.product.id for hit in await keyword_only.search(query, limit=10)
    ]

    assert [hit.product.id for hit in fallback_hits] == expected_ids
    assert expected_ids[0] == "ELE-001"


@pytest.mark.asyncio
async def test_query_time_embedding_failure_degrades_silently(
    full_adapter: JsonCatalogAdapter,
) -> None:
    # Vector index built successfully, but Ollama dies before answering
    # the query embedding - search must still work, keyword-only.
    index = await RetrievalIndex.build(
        full_adapter, BrokenEmbeddingProvider()
    )

    hits = await index.search("espresso machine", limit=5)

    assert hits[0].product.id == "HOM-001"


@pytest.mark.asyncio
async def test_vector_ranking_changes_fused_order(tmp_path) -> None:
    adapter = mini_adapter(tmp_path)
    keyword_only = await RetrievalIndex.build(adapter, None)
    hybrid = await RetrievalIndex.build(adapter, FakeEmbeddingProvider())

    query = "warm jacket"
    keyword_ids = [
        hit.product.id
        for hit in await keyword_only.search(query, limit=3)
    ]
    fused_ids = [
        hit.product.id for hit in await hybrid.search(query, limit=3)
    ]

    assert keyword_ids[0] == "A-001"
    # Sanity: the fake geometry makes B the vector-frontier (verified via
    # the provider below), so fusion must not simply echo keyword order.
    provider_vectors = await FakeEmbeddingProvider().embed(
        [query, product_document_of(adapter, "B-001")]
    )
    q, b = np.asarray(provider_vectors[0]), np.asarray(provider_vectors[1])
    assert float(q @ b) > 0.9, "B must be the closest vector match"
    assert fused_ids != keyword_ids
    assert fused_ids[0] == "B-001"
    assert set(fused_ids) == {"A-001", "B-001", "C-001"}


def product_document_of(adapter: JsonCatalogAdapter, product_id: str) -> str:
    product = adapter.get_product(product_id)
    assert product is not None
    return product_document(product)


@pytest.mark.asyncio
async def test_keyword_index_agrees_with_index_on_first_hit(
    full_adapter: JsonCatalogAdapter,
) -> None:
    keyword = KeywordIndex(full_adapter)
    index = await RetrievalIndex.build(full_adapter, None)

    keyword_ids = [pid for pid, _ in keyword.search("espresso", limit=3)]
    index_ids = [
        hit.product.id for hit in await index.search("espresso", limit=3)
    ]

    assert index_ids == keyword_ids
