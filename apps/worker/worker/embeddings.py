"""Bounded OpenAI embedding calls for P1 paraphrase recovery (ADR-002)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

MODEL = "text-embedding-3-small"


class EmbeddingUnavailable(RuntimeError):
    pass


async def cosine_scores(api_key: str, query: str, candidates: Sequence[tuple[str, str]]) -> dict[str, float]:
    """Embed one quote and a bounded local set of candidate paragraphs."""
    if not candidates:
        return {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": MODEL, "input": [query, *(text for _, text in candidates)]},
            )
    except httpx.HTTPError as exc:
        raise EmbeddingUnavailable("OpenAI embeddings request failed") from exc
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


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingUnavailable("OpenAI embeddings returned incompatible vector dimensions")
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    if denominator == 0:
        return 0.0
    return sum(x * y for x, y in zip(left, right, strict=True)) / denominator
