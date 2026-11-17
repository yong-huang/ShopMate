"""Optional vector index: embeddings via Ollama, disk-cached (3.4/ADR-003).

``EmbeddingProvider`` is the seam for embedding backends; the shipped
implementation talks to Ollama's ``/api/embed`` endpoint (bge-m3 by
default). Every failure mode - Ollama down, timeout, bad payload - raises
``ProviderUnavailable`` so callers degrade to keyword-only silently.

Embeddings are cached on disk (``backend/.embeddings/``, git-ignored),
keyed by ``sha256(model + "\\n" + text)``: regenerating the catalog or
re-running the index only embeds changed content.
"""

import hashlib
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_CACHE_DIR = Path(".embeddings")


class ProviderUnavailable(Exception):
    """The embedding backend could not be reached or returned garbage."""


class EmbeddingProvider(Protocol):
    """Seam for embedding backends (Ollama now; GLM via the gateway later)."""

    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, one vector per text, same order."""
        ...


class OllamaEmbeddingClient:
    """EmbeddingProvider over Ollama's batch /api/embed endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self._timeout_seconds = timeout_seconds
        self._cache_dir = cache_dir
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts``, serving cache hits locally first."""
        if not texts:
            return []
        if self._cache_dir is None:
            return await self._fetch(texts)

        vectors: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for index, text in enumerate(texts):
            cached = self._load_cached(text)
            if cached is None:
                missing.append(index)
            else:
                vectors[index] = cached
        if missing:
            fetched = await self._fetch([texts[i] for i in missing])
            for position, index in enumerate(missing):
                vector = fetched[position]
                vectors[index] = vector
                self._store_cached(texts[index], vector)
        return [vector for vector in vectors if vector is not None]

    async def _fetch(self, texts: list[str]) -> list[list[float]]:
        """POST the batch to Ollama; any failure is ProviderUnavailable."""
        client = self._get_client()
        try:
            response = await client.post(
                self._url, json={"model": self.model, "input": texts}
            )
            response.raise_for_status()
            embeddings = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            raise ProviderUnavailable(
                f"Ollama embeddings unavailable at {self._url}: {error}"
            ) from error
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ProviderUnavailable(
                f"Ollama returned malformed embeddings for {len(texts)} texts"
            )
        return embeddings

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _cache_path(self, text: str) -> Path:
        digest = hashlib.sha256(
            f"{self.model}\n{text}".encode("utf-8")
        ).hexdigest()
        return self._cache_dir / f"{digest}.npy"  # type: ignore[union-attr]

    def _load_cached(self, text: str) -> list[float] | None:
        if self._cache_dir is None:
            return None
        path = self._cache_path(text)
        if not path.is_file():
            return None
        return np.load(path).tolist()

    def _store_cached(self, text: str, vector: list[float]) -> None:
        if self._cache_dir is None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # float64 round-trips exactly, so cache hits equal fetched vectors.
        np.save(self._cache_path(text), np.asarray(vector, dtype=np.float64))


class VectorIndex:
    """Cosine-similarity index over one embedding per document."""

    def __init__(self, ids: list[str], matrix: np.ndarray) -> None:
        if len(ids) != matrix.shape[0]:
            raise ValueError("ids and matrix rows must align")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._ids = ids
        self._normalized = matrix / norms

    @classmethod
    async def build(
        cls, ids: list[str], documents: list[str], provider: EmbeddingProvider
    ) -> "VectorIndex":
        """Embed ``documents`` via ``provider`` and build the matrix."""
        vectors = await provider.embed(documents)
        return cls(ids=ids, matrix=np.asarray(vectors, dtype=np.float64))

    def search_by_vector(self, query: np.ndarray
                         ) -> list[tuple[str, float]]:
        """All documents as ``(id, cosine)`` sorted best-first."""
        norm = np.linalg.norm(query)
        if norm == 0:
            similarities = np.zeros(self._normalized.shape[0])
        else:
            similarities = self._normalized @ (query / norm)
        order = np.argsort(-similarities, kind="stable")
        return [(self._ids[int(row)], float(similarities[row]))
                for row in order]
