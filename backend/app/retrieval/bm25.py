"""Keyword index: bilingual tokenization + BM25 over the catalog (3.4).

Tokenization splits text into CJK runs (jieba word segmentation) and
latin/digit runs (whitespace/punctuation separated, lowercased). The
index documents are the System Design 3.4 doc recipe: bilingual name +
description + tags + category + key specs.
"""

import re

import jieba
from rank_bm25 import BM25Okapi

from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductDetail

_CJK_OR_LATIN = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+")

jieba.setLogLevel(60)  # silence the "Prefix dict has been built" log


def _is_cjk(text: str) -> bool:
    return "\u4e00" <= text[0] <= "\u9fff"


def tokenize(text: str) -> list[str]:
    """Tokenize mixed English/Chinese text for the BM25 index."""
    tokens: list[str] = []
    for match in _CJK_OR_LATIN.finditer(text):
        chunk = match.group(0)
        if _is_cjk(chunk):
            tokens.extend(token for token in jieba.lcut(chunk) if token.strip())
        else:
            tokens.append(chunk.lower())
    return tokens


def product_document(product: ProductDetail) -> str:
    """The retrieval document for one product (bilingual name/description,
    tags, category, key specs)."""
    parts = [
        product.name_en,
        product.name_zh,
        product.description_en,
        product.description_zh,
        product.category,
        " ".join(product.tags),
    ]
    parts.extend(
        f"{spec['label']} {spec['value']}" for spec in product.specs
    )
    return " ".join(parts)


class KeywordIndex:
    """BM25 index over the catalog; scores product ids for a query."""

    def __init__(self, adapter: CatalogAdapter) -> None:
        self._products: list[ProductDetail] = adapter.list_products()
        corpus = [tokenize(product_document(p)) for p in self._products]
        self._bm25 = BM25Okapi(corpus)
        self._ids = [p.id for p in self._products]

    def search(self, query: str, limit: int | None = None
               ) -> list[tuple[str, float]]:
        """Return ``(product_id, bm25_score)`` pairs, best first.

        Every indexed document is scored (System Design 3.4 "score
        candidates"); zero-scored documents sort to the tail so fusion
        still sees a complete, deterministic ranking.
        """
        scores = self._bm25.get_scores(tokenize(query))
        scored = [
            (self._ids[i], float(score))
            for i, score in enumerate(scores)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit] if limit is not None else scored
