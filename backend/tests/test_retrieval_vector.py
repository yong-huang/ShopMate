"""Tests for the embedding provider seam and vector index (T02, 3.4).

Ollama is an external system boundary, so it is faked with an
``httpx.MockTransport`` - no network access in tests. Disk cache uses
``tmp_path``.
"""

import httpx
import json
import numpy as np
import pytest

from app.retrieval.vector import (
    OllamaEmbeddingClient,
    ProviderUnavailable,
    VectorIndex,
)


def ollama_transport(handler):
    return httpx.MockTransport(handler)


def embedding_handler(request: httpx.Request) -> httpx.Response:
    """Fake /api/embed: deterministic 3-dim vectors keyed by input text."""
    payload = json.loads(request.read())
    assert "model" in payload
    vectors = []
    for text in payload["input"]:
        seed = sum(text.encode("utf-8")) % 97
        vectors.append([(seed + i) / 100 for i in range(3)])
    return httpx.Response(200, json={"embeddings": vectors})


@pytest.mark.asyncio
async def test_ollama_client_embeds_texts_with_model_and_batch_input() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return embedding_handler(request)

    client = OllamaEmbeddingClient(
        base_url="http://localhost:11434",
        model="bge-m3",
        transport=ollama_transport(handler),
        cache_dir=None,
    )

    vectors = await client.embed(["hello", "world"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 3
    assert len(requests) == 1
    assert requests[0].url.path == "/api/embed"


@pytest.mark.asyncio
async def test_ollama_client_raises_provider_unavailable_when_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OllamaEmbeddingClient(
        base_url="http://localhost:11434",
        model="bge-m3",
        transport=ollama_transport(handler),
        cache_dir=None,
    )

    with pytest.raises(ProviderUnavailable):
        await client.embed(["hello"])


@pytest.mark.asyncio
async def test_disk_cache_prevents_repeat_http_calls(tmp_path) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return embedding_handler(request)

    client = OllamaEmbeddingClient(
        base_url="http://localhost:11434",
        model="bge-m3",
        transport=ollama_transport(handler),
        cache_dir=tmp_path,
    )

    first = await client.embed(["hello", "world"])
    second = await client.embed(["hello", "world"])

    assert calls["count"] == 1, "second embed must be served from cache"
    assert first == second
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.asyncio
async def test_cache_is_keyed_by_model_and_text(tmp_path) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return embedding_handler(request)

    bge = OllamaEmbeddingClient(
        base_url="http://x", model="bge-m3",
        transport=ollama_transport(handler), cache_dir=tmp_path,
    )
    other = OllamaEmbeddingClient(
        base_url="http://x", model="other-model",
        transport=ollama_transport(handler), cache_dir=tmp_path,
    )

    await bge.embed(["hello"])
    await other.embed(["hello"])

    assert calls["count"] == 2, "same text under another model must re-embed"


def test_vector_index_ranks_by_cosine_similarity() -> None:
    # Hand-built geometry: q=[0.95, 0.05] is nearest doc2=[0.99, 0.01]
    # (cosine ~0.9991), then doc1=[1, 0] (~0.9986), doc3=[0, 1] (~0.053).
    index = VectorIndex(
        ids=["doc1", "doc2", "doc3"],
        matrix=np.array([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]]),
    )

    hits = index.search_by_vector(np.array([0.95, 0.05]))

    assert hits[0][0] == "doc2"
    assert hits[1][0] == "doc1"
    assert hits[0][1] > hits[1][1] > hits[2][1]
    assert {doc_id for doc_id, _ in hits} == {"doc1", "doc2", "doc3"}


@pytest.mark.asyncio
async def test_vector_index_builds_from_provider_embeddings() -> None:
    class FakeProvider:
        model = "fake"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    index = await VectorIndex.build(["a", "b"], ["a", "b"], FakeProvider())

    hits = index.search_by_vector(np.array([1.0, 0.0]))
    assert [doc_id for doc_id, _ in hits] == ["a", "b"]


def test_embedding_provider_factory_follows_the_config_chain(tmp_path, monkeypatch):
    """EMBEDDING_PROVIDER chain (3.4): auto/ollama -> Ollama client; none/glm -> degrade."""
    from app.config import Settings
    from app.retrieval.vector import (
        DEFAULT_TIMEOUT_SECONDS,
        OllamaEmbeddingClient,
        create_embedding_provider,
    )

    settings = Settings(_env_file=None, embedding_provider="none")
    assert create_embedding_provider(settings) is None

    settings = Settings(_env_file=None, embedding_provider="glm")
    assert create_embedding_provider(settings) is None  # GLM client not built yet

    settings = Settings(_env_file=None, embedding_provider="auto")
    provider = create_embedding_provider(settings)
    assert isinstance(provider, OllamaEmbeddingClient)
    assert provider.model == "bge-m3"
    # Index build embeds the whole catalog in one batch: the client timeout
    # must exceed the 2s per-request default or the vector half would
    # silently degrade on every real startup (found in live check).
    assert provider._timeout_seconds > DEFAULT_TIMEOUT_SECONDS
