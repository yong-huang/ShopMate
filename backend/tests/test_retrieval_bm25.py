"""Tests for retrieval tokenization and BM25 keyword search (T02, 3.4).

The seams under test:
- ``app.retrieval.bm25.tokenize`` - bilingual (jieba zh / latin en) tokenizer.
- ``KeywordIndex`` - BM25 index over the committed catalog documents.

Chinese-token identity is asserted behaviorally (CJK characters survive;
end-to-end queries find the right product), not against jieba's exact
dictionary output, which is an implementation detail of jieba.
"""

import pytest

from app.catalog.adapter import JsonCatalogAdapter, default_catalog_dir
from app.retrieval.bm25 import KeywordIndex, tokenize


@pytest.fixture(scope="module")
def index() -> KeywordIndex:
    return KeywordIndex(JsonCatalogAdapter(default_catalog_dir()))


def test_tokenizer_lowercases_and_splits_english() -> None:
    tokens = tokenize("Wireless Noise-Cancelling Headphones")

    assert tokens == ["wireless", "noise", "cancelling", "headphones"]


def test_tokenizer_keeps_all_cjk_characters_and_splits_chinese_words() -> None:
    tokens = tokenize("锁温保温杯 500ml")

    assert tokens == [*jieba_tokens("锁温保温杯"), "500ml"]


def jieba_tokens(text: str) -> list[str]:
    # Independent oracle: jieba's own cut of the CJK run.
    import jieba

    return jieba.lcut(text)


def test_tokenizer_handles_mixed_bilingual_text() -> None:
    tokens = tokenize("保温杯 insulated Water Bottle")

    assert "insulated" in tokens
    assert "water" in tokens
    assert "bottle" in tokens
    cjk = [t for t in tokens if any("\u4e00" <= ch <= "\u9fff" for ch in t)]
    assert "".join(cjk) == "保温杯"


def test_keyword_search_finds_noise_cancelling_headphones_first(
    index: KeywordIndex,
) -> None:
    hits = index.search("noise cancelling headphones", limit=3)

    assert hits
    assert hits[0][0] == "ELE-001", "Aurora X9 must top the ranking"
    assert hits[0][1] > 0


def test_keyword_search_handles_chinese_query(index: KeywordIndex) -> None:
    hits = index.search("保温杯", limit=5)

    assert hits[0][0] == "HOM-006", "ThermoLock flask must top the ranking"


def test_keyword_search_scores_every_document(index: KeywordIndex) -> None:
    hits = index.search("espresso machine", limit=150)

    assert len(hits) == 150
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0][0] == "HOM-001", "BrewMaster Pro must top the ranking"
