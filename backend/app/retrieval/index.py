"""Hybrid retrieval index: BM25 + optional vectors fused by RRF (3.4).

Query path (System Design 3.4):

1. Structured filters (category today; price/tags later via the agent
   tools) narrow the catalog to a candidate set.
2. Candidates are scored by BM25 and - when the embedding provider is
   reachable - by cosine similarity; both rankings are fused with
   Reciprocal Rank Fusion (k=60).
3. Any embedding failure (backend down at build or query time) degrades
   silently to keyword-only ranking.
4. Results are ``SearchHit`` records: product summary + fused score +
   ``match_hint`` explaining why the product fits, for the agent to
   reason over (and later to ground product cards).

Usage::

    index = await RetrievalIndex.build(adapter, embedding_provider)
    hits = await index.search("gift for a coffee lover", limit=5)

Chat tools (T04) call ``search``; the products REST endpoint uses the
adapter directly for plain storefront browsing.
"""

from typing import Any

import numpy as np
from pydantic import BaseModel

from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductDetail, ProductSummary
from app.retrieval.bm25 import KeywordIndex, product_document, tokenize
from app.retrieval.fusion import DEFAULT_K, reciprocal_rank_fusion
from app.retrieval.vector import EmbeddingProvider, ProviderUnavailable, VectorIndex


class SearchHit(BaseModel):
    """One retrieval result: the storefront summary plus score evidence."""

    product: ProductSummary
    score: float
    match_hint: str


class RetrievalIndex:
    """In-process hybrid index over the whole catalog."""

    def __init__(
        self,
        products: list[ProductDetail],
        keyword: KeywordIndex,
        vectors: VectorIndex | None,
        provider: EmbeddingProvider | None,
        k: int,
    ) -> None:
        self._products = products
        self._keyword = keyword
        self._vectors = vectors
        self._provider = provider
        self._k = k

    @classmethod
    async def build(
        cls,
        adapter: CatalogAdapter,
        embedding_provider: EmbeddingProvider | None = None,
        k: int = DEFAULT_K,
    ) -> "RetrievalIndex":
        """Index the catalog; embeddings are optional and best-effort.

        When the provider fails at build time (e.g. Ollama not running)
        the index is still fully functional keyword-only.
        """
        products = adapter.list_products()
        keyword = KeywordIndex(adapter)
        vectors: VectorIndex | None = None
        if embedding_provider is not None:
            try:
                vectors = await VectorIndex.build(
                    [product.id for product in products],
                    [product_document(product) for product in products],
                    embedding_provider,
                )
            except ProviderUnavailable:
                vectors = None  # silent fallback (ADR-003)
        return cls(products, keyword, vectors, embedding_provider, k)

    @property
    def vectors_enabled(self) -> bool:
        """Whether the vector half of the hybrid index is live."""
        return self._vectors is not None

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
        *,
        price_min: float | None = None,
        price_max: float | None = None,
        tags: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Search the catalog; see the module docstring for the recipe.

        Structured filters (System Design 3.4 step 1) narrow the candidate
        set before scoring: exact category, inclusive price band, tag
        conjunction (all tags must match), and exact attribute values.
        """
        candidates = [
            product for product in self._products
            if _passes_filters(
                product,
                category=category,
                price_min=price_min,
                price_max=price_max,
                tags=tags,
                attributes=attributes,
            )
        ]
        if not candidates:
            return []
        candidate_ids = {product.id for product in candidates}

        rankings: list[list[str]] = [
            [
                product_id
                for product_id, _ in self._keyword.search(query)
                if product_id in candidate_ids
            ]
        ]

        if self._vectors is not None and self._provider is not None:
            try:
                (query_vector,) = await self._provider.embed([query])
                vector_ranking = [
                    product_id
                    for product_id, _ in self._vectors.search_by_vector(
                        np.asarray(query_vector, dtype=np.float64)
                    )
                    if product_id in candidate_ids
                ]
                rankings.append(vector_ranking)
            except ProviderUnavailable:
                pass  # degrade to keyword-only silently (ADR-003)

        fused = reciprocal_rank_fusion(rankings, k=self._k)
        by_id = {product.id: product for product in candidates}
        return [
            SearchHit(
                product=ProductSummary.model_validate(
                    by_id[product_id].model_dump()
                ),
                score=score,
                match_hint=_match_hint(by_id[product_id], query),
            )
            for product_id, score in fused[:limit]
        ]


def _passes_filters(
    product: ProductDetail,
    *,
    category: str | None,
    price_min: float | None,
    price_max: float | None,
    tags: list[str] | None,
    attributes: dict[str, Any] | None,
) -> bool:
    """Structured candidate filter (System Design 3.4 step 1)."""
    if category is not None and product.category.casefold() != category.casefold():
        return False
    if price_min is not None and product.price < price_min:
        return False
    if price_max is not None and product.price > price_max:
        return False
    if tags:
        product_tags = {tag.casefold() for tag in product.tags}
        if not all(tag.casefold() in product_tags for tag in tags):
            return False
    if attributes:
        for key, value in attributes.items():
            actual = product.attributes.get(key)
            if actual is None or str(actual).casefold() != str(value).casefold():
                return False
    return True


def _match_hint(product: ProductDetail, query: str) -> str:
    """Explain why this product fits the query (deterministic heuristic)."""
    tokens = [token for token in tokenize(query) if len(token) > 1]

    def in_name() -> bool:
        return any(
            token in product.name_en.casefold() or token in product.name_zh
            for token in tokens
        )

    def in_tags() -> bool:
        return any(
            token in tag.casefold() for token in tokens for tag in product.tags
        )

    def in_specs() -> bool:
        return any(
            token in f"{spec['label']} {spec['value']}".casefold()
            for token in tokens
            for spec in product.specs
        )

    matched = [
        field for field, present in
        (("name", in_name()), ("tags", in_tags()), ("specs", in_specs()))
        if present
    ]
    if matched:
        return f"Matched {', '.join(matched)} for {query!r}"
    return "Semantically similar to your query"
