"""Best-effort Elastic indexing for P2's paragraph and finding analytics.

Postgres remains PinCite's source of truth.  These helpers are intentionally
side-effect-only adapters: callers catch :class:`ElasticUnavailable` and
continue the durable P1/P2 review flow when Elastic or its credentials are
absent.  They contain no verdict logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PARAGRAPHS_INDEX = "pincite-paragraphs"
FINDINGS_INDEX = "pincite-findings"
EMBEDDING_DIMS = 1536  # OpenAI text-embedding-3-small's default dimensions.


class ElasticUnavailable(RuntimeError):
    """Elastic could not safely perform an optional P2 operation."""


def create_elasticsearch_client(cloud_id: str | None, api_key: str | None) -> Any | None:
    """Build an async client only when deployment credentials are configured."""
    if not cloud_id or not api_key:
        return None
    # Keep this import lazy: local P1 execution remains usable before the
    # worker dependency is installed, and no client is constructed on import.
    from elastic_transport import HttpxAsyncHttpNode
    from elasticsearch import AsyncElasticsearch

    return AsyncElasticsearch(
        cloud_id=cloud_id,
        api_key=api_key,
        request_timeout=8.0,
        # Use the already-installed HTTPX transport, not elasticsearch's
        # optional aiohttp default (important for the Railway image).
        node_class=HttpxAsyncHttpNode,
    )


async def ensure_indices(client: Any) -> None:
    """Create P2 indices and mappings if absent; safe to invoke at startup."""
    try:
        await _ensure_index(client, PARAGRAPHS_INDEX, _paragraph_mapping())
        await _ensure_index(client, FINDINGS_INDEX, _finding_mapping())
    except ElasticUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - boundary normalises client errors
        raise ElasticUnavailable("unable to ensure Elastic indices") from exc


async def probe_rerank_inference_id(client: Any) -> str | None:
    """Best-effort startup probe for an Elastic hosted rerank endpoint.

    Elastic deployments differ in available inference products.  ADR-012
    requires discovery rather than assuming Jina/reranking exists; absence is
    a normal P2 fallback to application-side RRF.
    """

    try:
        response = await client.inference.get(task_type="rerank")
    except Exception:
        return None
    # The async client returns an ``ObjectApiResponse`` in production, while
    # the lightweight fakes used in unit tests are plain mappings.  Treat
    # both forms alike so a valid managed endpoint is not silently skipped.
    body = _response_body_or_none(response)
    endpoints = body.get("endpoints", []) if body is not None else []
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        if not isinstance(endpoint, Mapping):
            continue
        identifier = endpoint.get("inference_id") or endpoint.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
    return None


def _response_body_or_none(response: Any) -> Mapping[str, Any] | None:
    if isinstance(response, Mapping):
        return response
    body = getattr(response, "body", None)
    return body if isinstance(body, Mapping) else None


async def _ensure_index(client: Any, index: str, mappings: dict[str, Any]) -> None:
    try:
        exists = await client.indices.exists(index=index)
        if not exists:
            await client.indices.create(index=index, mappings=mappings)
    except Exception as exc:  # noqa: BLE001
        raise ElasticUnavailable(f"unable to ensure Elastic index {index}") from exc


async def index_source_paragraphs(
    client: Any,
    source: Any,
    paragraphs: Sequence[Any],
    *,
    embeddings_by_para: Mapping[int, Sequence[float]] | None = None,
) -> None:
    """Bulk-upsert source paragraphs under stable ``{source}:p{para}`` IDs.

    ``embeddings_by_para`` is optional solely for graceful degradation while
    OpenAI is unavailable.  Documents remain BM25-queryable; successful
    indexing callers should pass ADR-012 vectors to enable hybrid retrieval.
    """
    operations: list[dict[str, Any]] = []
    source_id = str(source.id)
    for paragraph in paragraphs:
        para_no = int(paragraph.para_no)
        document: dict[str, Any] = {
            "source_id": source_id,
            "case_name": source.case_name,
            "court": source.court,
            # Never invent a majority opinion when the upstream does not say.
            "opinion_part": paragraph.opinion_part or "unknown",
            "para_no": para_no,
            "page": paragraph.page,
            "text": paragraph.text,
        }
        vector = (embeddings_by_para or {}).get(para_no)
        if vector is not None:
            document["embedding"] = _validated_vector(vector)
        operations.extend(
            (
                {"index": {"_index": PARAGRAPHS_INDEX, "_id": paragraph_document_id(source_id, para_no)}},
                document,
            )
        )
    if not operations:
        return
    try:
        response = await client.bulk(operations=operations, refresh=False)
    except Exception as exc:  # noqa: BLE001
        raise ElasticUnavailable("unable to index source paragraphs") from exc
    if _response_body(response).get("errors"):
        raise ElasticUnavailable("Elastic rejected one or more source paragraphs")


async def index_finding(client: Any, finding: Any, citation: Any) -> None:
    """Upsert an analytics document without making findings depend on Elastic."""
    document = {
        "finding_id": str(finding.id),
        "citation_id": str(citation.id),
        "job_id": str(citation.job_id),
        "normalized_citation": citation.normalized,
        "check": finding.check,
        "verdict": finding.verdict,
        "confidence": finding.confidence,
        "notes": list(finding.notes or []),
        "evidence": finding.evidence or {},
        "created_at": finding.created_at.isoformat(),
    }
    try:
        await client.index(index=FINDINGS_INDEX, id=str(finding.id), document=document, refresh=False)
    except Exception as exc:  # noqa: BLE001
        raise ElasticUnavailable("unable to index finding") from exc


def paragraph_document_id(source_id: str, para_no: int) -> str:
    """Return the only accepted cross-system paragraph identifier (SPEC §7.2)."""
    return f"{source_id}:p{para_no}"


def _validated_vector(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    if len(values) != EMBEDDING_DIMS:
        raise ElasticUnavailable(
            f"paragraph embedding has {len(values)} dimensions; expected {EMBEDDING_DIMS}"
        )
    return values


def _response_body(response: Any) -> Mapping[str, Any]:
    """Handle both test dictionaries and elastic-transport response wrappers."""
    if isinstance(response, Mapping):
        return response
    body = getattr(response, "body", None)
    if isinstance(body, Mapping):
        return body
    # A malformed successful response is just as unusable as a transport
    # failure, and must remain an optional-indexing failure.
    raise ElasticUnavailable("Elastic returned an unexpected bulk response")


def _paragraph_mapping() -> dict[str, Any]:
    return {
        "properties": {
            "source_id": {"type": "keyword"},
            "case_name": {"type": "text"},
            "court": {"type": "keyword"},
            "opinion_part": {"type": "keyword"},
            "para_no": {"type": "integer"},
            "page": {"type": "integer"},
            "text": {"type": "text"},
            "embedding": {
                "type": "dense_vector",
                "dims": EMBEDDING_DIMS,
                "index": True,
                "similarity": "cosine",
            },
        }
    }


def _finding_mapping() -> dict[str, Any]:
    return {
        "properties": {
            "finding_id": {"type": "keyword"},
            "citation_id": {"type": "keyword"},
            "job_id": {"type": "keyword"},
            "normalized_citation": {"type": "text"},
            "check": {"type": "keyword"},
            "verdict": {"type": "keyword"},
            "confidence": {"type": "float"},
            "notes": {"type": "keyword"},
            "evidence": {"type": "object", "enabled": False},
            "created_at": {"type": "date"},
        }
    }
