"""Bounded OpenAI embedding calls for P1 paraphrase recovery (ADR-002)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

MODEL = "text-embedding-3-small"


class EmbeddingUnavailable(RuntimeError):
    pass


async def cosine_scores(
    api_key: str,
    query: str,
    candidates: Sequence[tuple[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, float]:
    """Embed one quote and a bounded local set of candidate paragraphs."""
    if not candidates:
        return {}
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8.0))
    try:
        response = await active_client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": MODEL, "input": [query, *(text for _, text in candidates)]},
        )
    except httpx.HTTPError as exc:
        raise EmbeddingUnavailable("OpenAI embeddings request failed") from exc
    finally:
        if owns_client:
            await active_client.aclose()
    if response.status_code != 200:
        raise EmbeddingUnavailable(f"OpenAI embeddings returned HTTP {response.status_code}")
    payload = response.json()
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if len(rows) != len(candidates) + 1:
        raise EmbeddingUnavailable("OpenAI embeddings returned an unexpected response")
    vectors = [row.get("embedding") for row in rows if isinstance(row, dict)]
    if len(vectors) != len(rows) or not all(isinstance(vector, list) for vector in vectors):
        raise EmbeddingUnavailable("OpenAI embeddings response had no vectors")
    query_vector = vectors[0]
    return {identifier: _cosine(query_vector, vector) for (identifier, _), vector in zip(candidates, vectors[1:], strict=True)}


async def embed_texts(
    api_key: str,
    texts: Sequence[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    """Return OpenAI embeddings in input order for the P2 Elastic index.

    This is intentionally the same bounded, failure-normalising request shape
    as :func:`cosine_scores`.  Callers decide whether an unavailable embedding
    service should fall back to lexical retrieval or mark their work
    unverifiable; this adapter never silently fabricates a vector.
    """
    if not texts:
        return []
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8.0))
    try:
        response = await active_client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": MODEL, "input": list(texts)},
        )
    except httpx.HTTPError as exc:
        raise EmbeddingUnavailable("OpenAI embeddings request failed") from exc
    finally:
        if owns_client:
            await active_client.aclose()
    if response.status_code != 200:
        raise EmbeddingUnavailable(f"OpenAI embeddings returned HTTP {response.status_code}")
    payload = response.json()
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if len(rows) != len(texts):
        raise EmbeddingUnavailable("OpenAI embeddings returned an unexpected response")
    vectors = [row.get("embedding") for row in rows if isinstance(row, dict)]
    if len(vectors) != len(rows) or not all(_is_numeric_vector(vector) for vector in vectors):
        raise EmbeddingUnavailable("OpenAI embeddings response had no vectors")
    return [[float(value) for value in vector] for vector in vectors]


def _is_numeric_vector(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, (int, float)) for item in value)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingUnavailable("OpenAI embeddings returned incompatible vector dimensions")
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    if denominator == 0:
        return 0.0
    return sum(x * y for x, y in zip(left, right, strict=True)) / denominator
