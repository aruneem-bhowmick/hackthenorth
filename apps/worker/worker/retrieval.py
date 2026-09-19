"""Elastic-backed paragraph retrieval for P2 proposition support (ADR-012)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from worker.elastic import EMBEDDING_DIMS, PARAGRAPHS_INDEX, ElasticUnavailable, paragraph_document_id
from worker.embeddings import EmbeddingUnavailable, embed_texts

RRF_K = 60


@dataclass(frozen=True)
class RetrievedParagraph:
    """A trusted, source-scoped paragraph suitable for the proposition judge."""

    para_id: str
    opinion_part: str
    text: str
    para_no: int
    page: int | None


EmbedQuery = Callable[[str], Awaitable[Sequence[float] | None]]
Rerank = Callable[[str, Sequence[RetrievedParagraph]], Awaitable[Sequence[RetrievedParagraph] | None]]


async def retrieve_paragraphs(
    client: Any,
    source_id: str,
    proposition_text: str,
    top_k: int,
    *,
    query_embedding: Sequence[float] | None = None,
    embed_query: EmbedQuery | None = None,
    rerank: Rerank | None = None,
) -> list[RetrievedParagraph]:
    """Return source-scoped BM25 + kNN results fused by application-side RRF.

    The optional ``embed_query`` seam lets worker orchestration share its
    OpenAI client/semaphore.  Without an embedding, lexical BM25 remains a
    safe best-effort fallback; an Elastic error is surfaced as the typed
    exception so callers can produce an honest ``UNVERIFIABLE`` finding.
    """
    if not source_id or not proposition_text or top_k <= 0:
        return []
    fetch_size = max(top_k, 1)
    try:
        lexical = await _search_bm25(client, source_id, proposition_text, fetch_size)
        vector = list(query_embedding) if query_embedding is not None else None
        if vector is None and embed_query is not None:
            vector = list(await embed_query(proposition_text) or [])
        semantic = (
            await _search_knn(client, source_id, vector, fetch_size)
            if _valid_query_vector(vector)
            else []
        )
    except ElasticUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ElasticUnavailable("unable to retrieve source paragraphs") from exc
    fused = reciprocal_rank_fusion(lexical, semantic, top_k=top_k)
    if rerank is None or not fused:
        return fused
    # A caller supplies this only after the one-time deployment capability
    # probe has confirmed a hosted reranker.  It is optional by design: a
    # failed/non-conforming rerank response keeps the already-valid RRF order.
    try:
        reranked = await rerank(proposition_text, fused)
    except Exception:  # noqa: BLE001 - best-effort sponsor capability
        return fused
    return _safe_rerank_order(fused, reranked)


async def retrieve_quote_candidates(
    client: Any, source_id: str, quote_text: str, limit: int = 24
) -> list[RetrievedParagraph]:
    """Return BM25-only quote candidates for ADR-010's P2 index swap.

    Quote fidelity remains deterministic: this changes only where its bounded
    candidate passages come from.  Callers must catch ``ElasticUnavailable``
    and retain P1's local candidate ranking when the optional index is down.
    """
    if not source_id or not quote_text or limit <= 0:
        return []
    try:
        return await _search_bm25(client, source_id, quote_text, limit)
    except ElasticUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ElasticUnavailable("unable to retrieve quote candidates") from exc


def hosted_reranker(client: Any, inference_id: str) -> Rerank:
    """Adapt Elastic's optional rerank inference response to trusted hits."""

    async def rerank(query: str, paragraphs: Sequence[RetrievedParagraph]) -> Sequence[RetrievedParagraph]:
        response = await client.inference.rerank(
            inference_id=inference_id,
            query=query,
            input=[item.text for item in paragraphs],
        )
        rows = response.get("rerank", []) if isinstance(response, dict) else []
        ordered: list[RetrievedParagraph] = []
        for row in rows if isinstance(rows, list) else []:
            index = row.get("index") if isinstance(row, dict) else None
            if isinstance(index, int) and 0 <= index < len(paragraphs):
                ordered.append(paragraphs[index])
        return ordered

    return rerank


def configured_openai_embedder(api_key: str | None) -> EmbedQuery | None:
    """Return an ADR-012 query embedder, or ``None`` if OpenAI is unavailable."""
    if not api_key:
        return None

    async def embed_query(text: str) -> Sequence[float] | None:
        try:
            vectors = await embed_texts(api_key, [text])
        except EmbeddingUnavailable:
            return None
        return vectors[0] if vectors else None

    return embed_query


async def _search_bm25(
    client: Any, source_id: str, proposition_text: str, size: int
) -> list[RetrievedParagraph]:
    response = await client.search(
        index=PARAGRAPHS_INDEX,
        size=size,
        query={
            "bool": {
                "must": [{"match": {"text": {"query": proposition_text}}}],
                "filter": [{"term": {"source_id": source_id}}],
            }
        },
        source=["para_no", "opinion_part", "page", "text"],
    )
    return _parse_hits(response, source_id)


async def _search_knn(
    client: Any, source_id: str, vector: Sequence[float], size: int
) -> list[RetrievedParagraph]:
    response = await client.search(
        index=PARAGRAPHS_INDEX,
        size=size,
        knn={
            "field": "embedding",
            "query_vector": list(vector),
            "k": size,
            "num_candidates": max(size * 10, 50),
            "filter": {"term": {"source_id": source_id}},
        },
        source=["para_no", "opinion_part", "page", "text"],
    )
    return _parse_hits(response, source_id)


def reciprocal_rank_fusion(
    lexical: Sequence[RetrievedParagraph],
    semantic: Sequence[RetrievedParagraph],
    *,
    top_k: int,
) -> list[RetrievedParagraph]:
    """Fuse ranked lists with the ADR-012 score ``Σ 1 / (60 + rank)``."""
    by_id: dict[str, RetrievedParagraph] = {}
    scores: dict[str, float] = {}
    first_rank: dict[str, int] = {}
    for ranked in (lexical, semantic):
        for rank, paragraph in enumerate(ranked, start=1):
            by_id.setdefault(paragraph.para_id, paragraph)
            scores[paragraph.para_id] = scores.get(paragraph.para_id, 0.0) + 1 / (RRF_K + rank)
            first_rank[paragraph.para_id] = min(first_rank.get(paragraph.para_id, rank), rank)
    ordered = sorted(scores, key=lambda para_id: (-scores[para_id], first_rank[para_id], para_id))
    return [by_id[para_id] for para_id in ordered[:top_k]]


def _safe_rerank_order(
    original: Sequence[RetrievedParagraph], reranked: Sequence[RetrievedParagraph] | None
) -> list[RetrievedParagraph]:
    """Accept only a permutation/subset of trusted RRF paragraphs from rerank."""
    if reranked is None:
        return list(original)
    allowed = {paragraph.para_id: paragraph for paragraph in original}
    result: list[RetrievedParagraph] = []
    seen: set[str] = set()
    for paragraph in reranked:
        if paragraph.para_id in allowed and paragraph.para_id not in seen:
            result.append(allowed[paragraph.para_id])
            seen.add(paragraph.para_id)
    # A partial response must not discard evidence. Preserve the RRF tail in
    # its stable order, never admitting a paragraph the judge was not given.
    result.extend(paragraph for paragraph in original if paragraph.para_id not in seen)
    return result


def _parse_hits(response: Any, source_id: str) -> list[RetrievedParagraph]:
    body = _response_body(response)
    try:
        hits = body["hits"]["hits"]
    except (KeyError, TypeError):
        raise ElasticUnavailable("Elastic search returned an unexpected response") from None
    if not isinstance(hits, list):
        raise ElasticUnavailable("Elastic search returned invalid hits")
    paragraphs: list[RetrievedParagraph] = []
    for hit in hits:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            continue
        try:
            para_no = int(source["para_no"])
            text = str(source["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text:
            continue
        page_value = source.get("page")
        try:
            page = int(page_value) if page_value is not None else None
        except (TypeError, ValueError):
            page = None
        paragraphs.append(
            RetrievedParagraph(
                para_id=paragraph_document_id(source_id, para_no),
                opinion_part=str(source.get("opinion_part") or "unknown"),
                text=text,
                para_no=para_no,
                page=page,
            )
        )
    return paragraphs


def _valid_query_vector(vector: Sequence[float] | None) -> bool:
    return bool(vector) and len(vector) == EMBEDDING_DIMS and all(
        isinstance(value, (int, float)) for value in vector
    )


def _response_body(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    body = getattr(response, "body", None)
    if isinstance(body, dict):
        return body
    raise ElasticUnavailable("Elastic search returned an unexpected response")
