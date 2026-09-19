"""P1 asynchronous pipeline orchestration.

The job task only ingests and persists brief facts.  Each citation then runs as
its own retry-safe arq task, so a slow CourtListener response cannot block
otherwise independent citations (FR-SYS-002).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import sentry_sdk
from sentry_sdk import logger as sentry_logger
import yaml
from investigator.agent import (
    CitationContext as InvestigatorCitationContext,
    DirectOfficialCandidate,
    ExtractedCandidate as InvestigatorCandidate,
    InvestigationBudget,
    InvestigationVerdict,
    InvestigatorUnavailable,
    run_investigation,
)
from investigator.official_sources import (
    OfficialUrlResolverRule,
    resolve_official_urls,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pipeline import extract_citations, extract_pdf
from pipeline.ingestion import IngestionError
from pipeline.models import AntecedentState
from verdicts import SourceParagraph as VerdictParagraph
from verdicts import check_quote, normalise
from verdicts.existence import (
    CitationContext as MatchCitationContext,
    ExistenceVerdict,
    ExtractedCandidate as MatchCandidate,
    case_name_similarity,
    load_investigator_thresholds,
    match_candidate,
)
from verdicts.proposition import (
    JudgeParagraph,
    PropositionVerdict,
    evaluate_proposition,
    load_proposition_thresholds,
)

from worker.config import get_settings
from worker.courtlistener import (
    MAX_CITATIONS_PER_REQUEST,
    MAX_TEXT_CHARS_PER_REQUEST,
    CitationLookup,
    CourtListenerClient,
    CourtListenerUnavailable,
)
from worker.embeddings import EmbeddingUnavailable, cosine_scores, embed_texts
from worker.gptzero import (
    GPTZeroScore,
    score_ai_likelihood_result,
    score_hallucination_result,
)
from worker.elastic import (
    ElasticUnavailable,
    create_elasticsearch_client,
    ensure_indices,
    index_finding,
    index_source_paragraphs,
    probe_rerank_inference_id,
)
from worker.judge import JudgeUnavailable, call_judge
from worker.proposition_extraction import (
    PropositionExcerpt,
    PropositionExtractionUnavailable,
    extract_propositions,
)
from worker.retrieval import (
    RetrievedParagraph,
    hosted_reranker,
    retrieve_paragraphs,
    retrieve_quote_candidates,
)
from worker.checks import CHECK_EXISTENCE, CHECK_PROPOSITION, CHECK_QUOTE
from worker.db import (
    BriefPage,
    Claim,
    Citation,
    Finding,
    Job,
    JobStatus,
    InvestigatorRun,
    LookupCache,
    Provenance,
    Source,
    SourceAcquisition,
    SourceParagraph,
    Signal,
    get_sessionmaker,
)
from worker.rate_limit import (
    COURTLISTENER_VALID_CITATIONS_PER_MINUTE,
    CourtListenerRateLimiter,
)
from worker.sse import get_redis, publish_event


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "br", "div", "li", "blockquote", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def text(self) -> str:
        return "".join(self.parts)


def _clean_opinion_text(opinion: dict[str, Any]) -> str:
    value = (
        opinion.get("html_with_citations")
        or opinion.get("plain_text")
        or opinion.get("html")
        or ""
    )
    if not isinstance(value, str):
        return ""
    if "<" not in value:
        return value.strip()
    parser = _TextExtractor()
    parser.feed(value)
    return parser.text().strip()


def _paragraph_texts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


@dataclass(frozen=True)
class SourceMaterial:
    """Network-fetched opinion text, kept outside a database transaction."""

    cluster_id: str
    case_name: str | None
    court: str | None
    paragraphs: list[tuple[str, str | None]]


@dataclass(frozen=True)
class ResolutionResult:
    """The next durable stage after a CourtListener lookup."""

    source_cluster_id: str | None = None
    source_ready: bool = False
    needs_investigation: bool = False


def _opinion_part(opinion_type: object) -> str:
    """Map CourtListener's documented Opinion.type choices conservatively."""

    value = str(opinion_type or "")
    if value.startswith(("010combined", "015unamimous", "020lead", "025plurality")):
        return "majority"
    if value.startswith("030concurrence"):
        return "concurrence"
    if value.startswith("040dissent"):
        return "dissent"
    # In-part, procedural and absent types are not reliable majority evidence.
    return "unknown"


async def startup(ctx: dict[str, Any]) -> None:
    """Reuse CourtListener connections and cap independent source downloads."""
    ctx["courtlistener_http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
        limits=httpx.Limits(max_connections=6, max_keepalive_connections=6),
    )
    ctx["source_fetch_semaphore"] = asyncio.Semaphore(4)
    # Browserbase sessions are slow and billable; investigation remains
    # independent per citation but intentionally bounded in parallelism.
    ctx["investigator_semaphore"] = asyncio.Semaphore(2)
    ctx["openai_http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
    )
    ctx["gptzero_http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
    )
    ctx["embedding_semaphore"] = asyncio.Semaphore(8)
    settings = get_settings()
    elastic = create_elasticsearch_client(
        settings.elastic_cloud_id, settings.elastic_api_key
    )
    if elastic is not None:
        try:
            await ensure_indices(elastic)
        except ElasticUnavailable:
            # Elastic must never keep P1's durable pipeline from starting.
            await elastic.close()
        else:
            ctx["elastic"] = elastic
            rerank_id = await probe_rerank_inference_id(elastic)
            if rerank_id:
                ctx["elastic_reranker"] = hosted_reranker(elastic, rerank_id)


async def shutdown(ctx: dict[str, Any]) -> None:
    client = ctx.get("courtlistener_http")
    if client is not None:
        await client.aclose()
    client = ctx.get("openai_http")
    if client is not None:
        await client.aclose()
    client = ctx.get("gptzero_http")
    if client is not None:
        await client.aclose()
    client = ctx.get("elastic")
    if client is not None:
        await client.close()


async def process_job(
    ctx: dict[str, Any],
    job_id: str,
    sentry_trace: str | None = None,
    sentry_baggage: str | None = None,
) -> None:
    headers = {}
    if sentry_trace:
        headers["sentry-trace"] = sentry_trace
    if sentry_baggage:
        headers["baggage"] = sentry_baggage
    transaction = sentry_sdk.continue_trace(
        headers, op="queue.task", name="process_job"
    )
    with sentry_sdk.start_transaction(transaction) as txn:
        txn.set_tag("job_id", job_id)
        await _ingest_and_enqueue(ctx, job_id)


async def _ingest_and_enqueue(ctx: dict[str, Any], job_id: str) -> None:
    """Extract a brief once, persist citation facts, then fan out citations."""

    sessionmaker = get_sessionmaker()
    settings = get_settings()
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None or job.status == JobStatus.COMPLETED.value:
            return
        try:
            job.status = JobStatus.PROCESSING.value
            await session.commit()
            await publish_event(job_id, "job.status", {"status": job.status})

            pdf_path = Path(settings.uploads_dir) / f"{job_id}.pdf"
            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                raise IngestionError(
                    "UNSUPPORTED_FILE", "The uploaded PDF could not be found."
                )
            document = extract_pdf(pdf_path)
            job.page_count = document.page_count
            await _persist_brief_pages(session, job.id, document)
            extracted = extract_citations(document)
            persisted = await _persist_citations(session, job.id, extracted)
            try:
                await _prime_proposition_extraction(
                    session, persisted, extracted, document, ctx.get("openai_http")
                )
            except PropositionExtractionUnavailable:
                # Proposition extraction is best effort.  Per-citation work
                # writes a terminal UNVERIFIABLE result when it is unavailable.
                pass
            # FR-RES-001: resolve distinct full citations in bounded CourtListener
            # batches before citation fan-out.  Short forms reuse their
            # antecedent's cache entry in their independent task.
            try:
                await _prime_lookup_cache(session, persisted)
            except CourtListenerUnavailable:
                # The independent tasks retain their per-citation failure
                # isolation and will record neutral unavailable findings.
                # A transient batch failure must not fail the whole brief.
                pass
            await session.commit()
            await publish_event(
                job_id,
                "citations.extracted",
                {
                    "count": len(persisted),
                    "citations": [
                        {
                            "id": str(citation.id),
                            "page": citation.page,
                            "start": citation.start_offset,
                            "end": citation.end_offset,
                        }
                        for citation in persisted
                    ],
                },
            )
            # FR-SIG-002: run page scoring outside ingestion and the review
            # lifecycle. A slow/unavailable provider cannot postpone the
            # first citation update or terminal verdicts.
            await ctx["redis"].enqueue_job(
                "process_page_signals",
                job_id,
                _job_id=f"page-signals:{job_id}",
            )
            if not persisted:
                await _complete_job(session, job)
                await session.commit()
                await publish_event(job_id, "job.completed", {"summary": {}})
                return

            for citation in persisted:
                await ctx["redis"].enqueue_job(
                    "process_citation",
                    str(citation.id),
                    _job_id=f"{job_id}:citation:{citation.id}",
                )
        except IngestionError as exc:
            await session.rollback()
            job.status = JobStatus.FAILED.value
            await session.commit()
            await publish_event(
                job_id, "job.failed", {"error": {"code": exc.code, "message": str(exc)}}
            )
        except Exception as exc:  # noqa: BLE001 -- no unexpected failure may strand a job
            await session.rollback()
            job.status = JobStatus.FAILED.value
            await session.commit()
            await publish_event(
                job_id,
                "job.failed",
                {
                    "error": {
                        "code": "UPSTREAM_UNAVAILABLE",
                        "message": "The job could not be processed.",
                    }
                },
            )
            raise exc


async def _persist_citations(
    session: Any, job_id: uuid.UUID, extracted: Any
) -> list[Citation]:
    """Persist processing-neutral citation records with stable antecedent links."""

    existing = await session.scalars(select(Citation).where(Citation.job_id == job_id))
    existing_items = list(existing)
    if existing_items:
        return existing_items
    records: list[Citation] = []
    by_pipeline_id: dict[str, Citation] = {}
    for item in extracted:
        record = Citation(
            job_id=job_id,
            raw_text=item.raw_text,
            normalized=item.normalized,
            kind=item.kind.value,
            page=item.original_span.page,
            start_offset=item.original_span.start,
            end_offset=item.original_span.end,
            pinpoint=", ".join(item.pinpoint) or None,
            case_name=item.case_name,
            court_hint=item.court_hint,
            year_hint=item.year_hint,
            resolution_state=item.antecedent_state.value,
        )
        session.add(record)
        records.append(record)
        by_pipeline_id[item.id] = record
    await session.flush()
    for record, item in zip(records, extracted, strict=True):
        if item.antecedent_id and item.antecedent_id in by_pipeline_id:
            record.antecedent_id = by_pipeline_id[item.antecedent_id].id
        # P2 has exactly one claim row per citation. It carries both the
        # optional P1 quotation and the subsequently extracted proposition.
        session.add(
            Claim(
                citation_id=record.id,
                quote_text=item.quote.text if item.quote else None,
                quote_start=item.quote.original_span.start if item.quote else None,
                quote_end=item.quote.original_span.end if item.quote else None,
                quote_processing_start=item.quote.processing_span.start
                if item.quote
                else None,
                quote_processing_end=item.quote.processing_span.end
                if item.quote
                else None,
            )
        )
    return records


async def _prime_proposition_extraction(
    session: Any,
    persisted: list[Citation],
    extracted: Any,
    document: Any,
    client: httpx.AsyncClient | None,
) -> None:
    """Extract all propositions before citation fan-out (FR-EXT-005)."""

    settings = get_settings()
    if not settings.openai_api_key or not persisted:
        return
    pages = {item.page: item.raw_text for item in document.pages}
    excerpts = [
        PropositionExcerpt(
            str(record.id),
            pages[item.context_span.page][
                item.context_span.start : item.context_span.end
            ],
        )
        for record, item in zip(persisted, extracted, strict=True)
    ]
    # Raw httpx calls are not covered by Sentry's OpenAI auto-integration.
    # Keep token/model telemetry at the provider boundary without recording
    # proposition or brief text (NFR-OBS-003 / NFR-PRIV-002).
    with sentry_sdk.start_span(
        op="ai.generate_text", name="openai.proposition_extraction"
    ) as span:
        span.set_tag("gen_ai.system", "openai")
        span.set_tag("gen_ai.operation.name", "proposition_extraction")

        def capture_usage(
            model: str, prompt_tokens: int | None, completion_tokens: int | None
        ) -> None:
            span.set_tag("gen_ai.request.model", model)
            if prompt_tokens is not None:
                span.set_data("gen_ai.usage.input_tokens", prompt_tokens)
            if completion_tokens is not None:
                span.set_data("gen_ai.usage.output_tokens", completion_tokens)

        propositions = await extract_propositions(
            settings.openai_api_key, excerpts, client, on_usage=capture_usage
        )
    for record, item in zip(persisted, extracted, strict=True):
        proposition = propositions.get(str(record.id))
        if proposition is None:
            continue
        claim = await session.scalar(
            select(Claim).where(Claim.citation_id == record.id)
        )
        if claim is None:
            continue
        context = pages[item.context_span.page][
            item.context_span.start : item.context_span.end
        ]
        offset = _recover_proposition_offset(context, proposition)
        if offset is None:
            prop_start, prop_end = item.context_span.start, item.context_span.end
        else:
            prop_start = item.context_span.start + offset[0]
            prop_end = item.context_span.start + offset[1]
        claim.proposition_text = proposition
        claim.prop_start = prop_start
        claim.prop_end = prop_end


def _recover_proposition_offset(
    context: str, proposition: str
) -> tuple[int, int] | None:
    """Recover real offsets from document text; never use model offsets."""

    direct = context.casefold().find(proposition.casefold())
    if direct >= 0:
        return direct, direct + len(proposition)
    terms = [re.escape(part) for part in normalise(proposition).split()]
    if not terms:
        return None
    match = re.search(r"\s+".join(terms), context, flags=re.IGNORECASE)
    return (match.start(), match.end()) if match else None


async def _prime_lookup_cache(session: Any, citations: list[Citation]) -> None:
    """Resolve each uncached full citation in CourtListener-sized batches.

    This is deliberately pre-fan-out: it fulfills FR-RES-001 while retaining
    FR-SYS-002's independent source/quote task for every citation occurrence.
    A brief can contain many repeat or short citations, but it should make at
    most one lookup request for each distinct full normalized citation.
    """

    settings = get_settings()
    if not settings.courtlistener_api_token:
        return
    unique_targets = {
        citation.normalized: citation
        for citation in citations
        if citation.kind == "full"
        and citation.resolution_state != AntecedentState.UNRESOLVED.value
    }
    if not unique_targets:
        return
    cached = set(
        await session.scalars(
            select(LookupCache.normalized_citation).where(
                LookupCache.normalized_citation.in_(tuple(unique_targets))
            )
        )
    )
    pending = [
        citation
        for normalized, citation in unique_targets.items()
        if normalized not in cached
    ]
    if not pending:
        return

    limiter = CourtListenerRateLimiter(get_redis())
    client = CourtListenerClient(
        settings.courtlistener_api_token, acquire_rate_limit=limiter.acquire
    )
    for batch in _citation_batches(pending):
        text, spans = _batch_text(batch)
        lookups = await client.lookup_text(text, expected_citations=len(batch))
        payloads = _lookup_payloads_for_batch(batch, spans, lookups)
        for citation in batch:
            payload = payloads[citation.id]
            session.add(
                LookupCache(
                    normalized_citation=citation.normalized,
                    status=str(payload["status"]),
                    response=payload,
                )
            )
    await session.flush()


def _citation_batches(citations: list[Citation]) -> list[list[Citation]]:
    """Partition citations without exceeding CourtListener's published caps."""

    batches: list[list[Citation]] = []
    current: list[Citation] = []
    chars = 0
    for citation in citations:
        text = citation.raw_text.strip() or citation.normalized
        size = len(text) + (1 if current else 0)
        if len(text) > MAX_TEXT_CHARS_PER_REQUEST:
            raise ValueError("a normalized citation exceeds CourtListener's text limit")
        if current and (
            len(current)
            == min(MAX_CITATIONS_PER_REQUEST, COURTLISTENER_VALID_CITATIONS_PER_MINUTE)
            or chars + size > MAX_TEXT_CHARS_PER_REQUEST
        ):
            batches.append(current)
            current, chars = [], 0
            size = len(text)
        current.append(citation)
        chars += size
    if current:
        batches.append(current)
    return batches


def _batch_text(
    citations: list[Citation],
) -> tuple[str, dict[uuid.UUID, tuple[int, int]]]:
    parts: list[str] = []
    spans: dict[uuid.UUID, tuple[int, int]] = {}
    offset = 0
    for citation in citations:
        text = citation.raw_text.strip() or citation.normalized
        if parts:
            offset += 1  # newline separator
        start = offset
        offset += len(text)
        spans[citation.id] = (start, offset)
        parts.append(text)
    return "\n".join(parts), spans


def _lookup_payload(lookup: CitationLookup) -> dict[str, Any]:
    return {
        "citation": lookup.citation,
        "normalized_citations": list(lookup.normalized_citations),
        "status": lookup.status,
        "error_message": lookup.error_message,
        "clusters": list(lookup.clusters),
    }


def _lookup_payloads_for_batch(
    citations: list[Citation],
    spans: dict[uuid.UUID, tuple[int, int]],
    lookups: list[CitationLookup],
) -> dict[uuid.UUID, dict[str, Any]]:
    """Associate CourtListener offset results with their input citations."""

    payloads: dict[uuid.UUID, dict[str, Any]] = {}
    for lookup in lookups:
        for citation in citations:
            start, end = spans[citation.id]
            if start <= lookup.start_index < end:
                payloads.setdefault(citation.id, _lookup_payload(lookup))
                break
    # CourtListener can omit text that its parser cannot recognize. Preserve a
    # neutral P1 outcome for that individual cite instead of dropping it.
    for citation in citations:
        payloads.setdefault(
            citation.id,
            {
                "citation": citation.raw_text,
                "normalized_citations": [],
                "status": 400,
                "error_message": "CourtListener did not recognize this citation in the request batch.",
                "clusters": [],
            },
        )
    return payloads


async def _persist_brief_pages(session: Any, job_id: uuid.UUID, document: Any) -> None:
    existing = await session.scalar(
        select(BriefPage.id).where(BriefPage.job_id == job_id).limit(1)
    )
    if existing is not None:
        return
    session.add_all(
        [
            BriefPage(job_id=job_id, page=item.page, text=item.raw_text)
            for item in document.pages
        ]
    )
    # Persist extracted brief text before citation fan-out makes it reviewable.
    await session.flush()


async def process_page_signals(ctx: dict[str, Any], job_id: str) -> None:
    """Score persisted brief pages in an independent, non-lifecycle task."""

    await _score_brief_page_signals_best_effort(ctx, uuid.UUID(job_id))


async def _score_brief_page_signals_best_effort(
    ctx: dict[str, Any], job_id: uuid.UUID
) -> None:
    """Store P4 page-level AI-writing signals without touching findings.

    A small concurrency limit keeps a large brief from creating an upstream
    burst.  The provider client itself converts every unavailable or malformed
    result to ``None``; this outer guard preserves the same non-blocking
    contract if persistence or a future adapter ever misbehaves.
    """

    api_key = get_settings().gptzero_api_key
    if not api_key:
        return
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        pages = list(
            await session.scalars(
                select(BriefPage)
                .where(BriefPage.job_id == job_id)
                .order_by(BriefPage.page)
            )
        )
    semaphore = asyncio.Semaphore(4)

    async def score_page(page: BriefPage) -> tuple[BriefPage, GPTZeroScore | None]:
        async with semaphore:
            return (
                page,
                await score_ai_likelihood_result(
                    api_key, page.text, client=ctx.get("gptzero_http")
                ),
            )

    try:
        scored_pages = await asyncio.gather(
            *(score_page(page) for page in pages)
        )
        async with sessionmaker() as session:
            for page, scored in scored_pages:
                if scored is None:
                    continue
                await _write_signal(
                    session,
                    job_id=job_id,
                    citation_id=None,
                    section_ref=f"page:{page.page}",
                    provider="gptzero",
                    kind="ai_likelihood",
                    score=scored.score,
                    raw=scored.raw,
                )
            await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        # Signals must never delay a usable P1--P3 review outcome or make its
        # job fail. The exception is observable, while its provider payload
        # and brief text stay out of Sentry tags/messages.
        sentry_sdk.capture_exception()


async def process_citation(ctx: dict[str, Any], citation_id: str) -> None:
    """Persist an existence result before any opinion download begins."""

    sessionmaker = get_sessionmaker()
    resolution = ResolutionResult()
    async with sessionmaker() as session:
        citation = await session.get(Citation, uuid.UUID(citation_id))
        if citation is None:
            return
        citation_pk = citation.id
        job_pk = citation.job_id
        with sentry_sdk.start_span(
            op="citation.verify", name="process_citation"
        ) as span:
            span.set_tag("job_id", str(citation.job_id))
            span.set_tag("citation_id", citation_id)
            try:
                resolution = await _resolve_citation(session, citation)
                await session.commit()
            except Exception:  # keep one citation from blocking all others
                await session.rollback()
                # Rollback expires ORM objects. Reload rather than reading an
                # expired attribute (which would trigger forbidden implicit IO
                # in SQLAlchemy's async ORM).
                citation = await session.get(Citation, citation_pk)
                if citation is None:
                    return
                await _write_finding(
                    session,
                    citation,
                    check=CHECK_EXISTENCE,
                    verdict="UNVERIFIABLE",
                    confidence=None,
                    notes=["UPSTREAM_UNAVAILABLE"],
                    evidence={},
                )
                await _write_proposition_unavailable(
                    session, citation, "UPSTREAM_UNAVAILABLE"
                )
                await session.commit()
            await _publish_citation_findings(session, citation, ctx.get("elastic"))
            await _maybe_finish_job(session, job_pk)
    # Queue after the short resolution transaction has committed.  A slow
    # source can therefore never replace or delay an already-known authority.
    if resolution.source_cluster_id:
        await ctx["redis"].enqueue_job(
            "process_source",
            resolution.source_cluster_id,
            # Database acquisition state is the durable cross-job dedupe key.
            # ARQ's job key expires much later, so it must be per scheduling
            # attempt rather than a permanent cluster-wide lock.
            _job_id=f"source:{citation_id}",
            _defer_by=-60,
        )
    elif resolution.source_ready:
        # A completed durable source needs no new acquisition task. Both
        # downstream checks remain independently idempotent.
        await ctx["redis"].enqueue_job(
            "process_quote", citation_id, _job_id=f"quote:{citation_id}"
        )
        await ctx["redis"].enqueue_job(
            "process_proposition", citation_id, _job_id=f"proposition:{citation_id}"
        )
    elif resolution.needs_investigation:
        await ctx["redis"].enqueue_job(
            "process_investigation", citation_id, _job_id=f"investigation:{citation_id}"
        )


async def _resolve_citation(session: Any, citation: Citation) -> ResolutionResult:
    """Return source work to queue, or whether a local source can be checked."""
    if citation.resolution_state == AntecedentState.UNRESOLVED.value:
        await _write_finding(
            session,
            citation,
            CHECK_EXISTENCE,
            "UNVERIFIABLE",
            None,
            ["UNRESOLVED_REFERENCE"],
            {},
        )
        # A quote cannot be aligned without a resolved source.  It must still
        # reach the approved terminal state so this one short-form reference
        # cannot keep the entire brief in ``processing`` (NFR-REL-001).
        await _write_quote_unavailable(session, citation)
        await _write_proposition_unavailable(session, citation, "UNRESOLVED_REFERENCE")
        return ResolutionResult()
    target = citation
    if citation.antecedent_id:
        antecedent = await session.get(Citation, citation.antecedent_id)
        if antecedent is None:
            await _write_finding(
                session,
                citation,
                CHECK_EXISTENCE,
                "UNVERIFIABLE",
                None,
                ["UNRESOLVED_REFERENCE"],
                {},
            )
            await _write_quote_unavailable(session, citation)
            await _write_proposition_unavailable(
                session, citation, "UNRESOLVED_REFERENCE"
            )
            return ResolutionResult()
        target = antecedent

    cache = await session.scalar(
        select(LookupCache).where(LookupCache.normalized_citation == target.normalized)
    )
    lookup_payload: dict[str, Any]
    source: Source | None = (
        await session.get(Source, cache.source_id)
        if cache and cache.source_id
        else None
    )
    if cache is not None:
        lookup_payload = cache.response
    else:
        settings = get_settings()
        if not settings.courtlistener_api_token:
            raise CourtListenerUnavailable("CourtListener is not configured")
        limiter = CourtListenerRateLimiter(get_redis())
        client = CourtListenerClient(
            settings.courtlistener_api_token, acquire_rate_limit=limiter.acquire
        )
        lookups = await client.lookup_text(target.raw_text, expected_citations=1)
        if lookups:
            lookup = lookups[0]
            lookup_payload = _lookup_payload(lookup)
            lookup_status = lookup.status
        else:
            # CourtListener did not recognise a parsed citation. P3 still
            # performs the bounded official-source investigation required by
            # FR-INV-001 instead of treating this as a terminal outcome.
            lookup_payload = {"status": 400, "clusters": []}
            lookup_status = 400
        cache = LookupCache(
            normalized_citation=target.normalized,
            status=str(lookup_status),
            response=lookup_payload,
        )
        session.add(cache)
        await session.flush()

    status = int(lookup_payload["status"])
    sentry_logger.info(
        "CourtListener citation resolution completed",
        attributes={
            "pincite.courtlistener.status": status,
            "pincite.citation_id": str(citation.id),
        },
    )
    if status == 200:
        citation.resolution_state = "resolved"
        await _write_finding(
            session,
            citation,
            CHECK_EXISTENCE,
            "VERIFIED",
            1.0,
            [],
            {"source_id": str(source.id)} if source else {},
        )
        if citation.antecedent_id:
            citation.resolution_state = "resolved_via_antecedent"
        # FR-RES-004 (P2): every resolved authority gets a locally persisted
        # opinion, even when the brief did not quote it.
        clusters = lookup_payload.get("clusters", [])
        if (
            not clusters
            or not isinstance(clusters[0], dict)
            or not clusters[0].get("id")
        ):
            await _write_quote_unavailable(session, citation)
            await _write_proposition_unavailable(
                session, citation, "SOURCE_UNAVAILABLE"
            )
            return ResolutionResult()
        cluster = clusters[0]
        cluster_id = str(cluster["id"])
        acquisition = await session.scalar(
            select(SourceAcquisition).where(SourceAcquisition.cluster_id == cluster_id)
        )
        if acquisition is None:
            acquisition = SourceAcquisition(
                cluster_id=cluster_id,
                state="queued",
                source_url=f"https://www.courtlistener.com/api/rest/v4/clusters/{cluster_id}/",
            )
            # Same-opinion citations are deliberately independent stage-one
            # tasks.  The unique cluster key arbitrates their race without
            # turning the losing citation into an upstream failure.
            try:
                async with session.begin_nested():
                    session.add(acquisition)
                    await session.flush()
            except IntegrityError:
                acquisition = await session.scalar(
                    select(SourceAcquisition).where(
                        SourceAcquisition.cluster_id == cluster_id
                    )
                )
                if acquisition is None:
                    raise
        citation.source_acquisition_id = acquisition.id
        if source is not None:
            acquisition.source_id = source.id
            acquisition.state = "fetched"
            acquisition.retrieved_at = acquisition.retrieved_at or datetime.now(
                timezone.utc
            )
            return ResolutionResult(source_ready=True)
        if acquisition.state == "fetched" and acquisition.source_id:
            return ResolutionResult(source_ready=True)
        if acquisition.state == "unavailable":
            await _write_quote_unavailable(session, citation)
            await _write_proposition_unavailable(
                session, citation, "SOURCE_UNAVAILABLE"
            )
            return ResolutionResult()
        return ResolutionResult(source_cluster_id=cluster_id)
    elif status == 404:
        citation.resolution_state = "not_in_database"
        await _write_pending_investigation(session, citation, status)
        return ResolutionResult(needs_investigation=True)
    elif status == 300:
        matching_clusters = _disambiguate_clusters(
            citation, lookup_payload.get("clusters", [])
        )
        if len(matching_clusters) == 1:
            resolved_payload = dict(lookup_payload)
            resolved_payload["status"] = 200
            resolved_payload["clusters"] = matching_clusters
            # Re-enter the normal CourtListener-source path without treating a
            # plausible candidate as verified until name/year disambiguation
            # has selected exactly one cluster (FR-RES-003).
            cache.response = resolved_payload
            return await _resolve_citation(session, citation)
        citation.resolution_state = "ambiguous"
        await _write_pending_investigation(
            session, citation, status, lookup_payload.get("clusters", [])
        )
        return ResolutionResult(needs_investigation=True)
    elif status == 400:
        citation.resolution_state = "unrecognized"
        await _write_pending_investigation(session, citation, status)
        return ResolutionResult(needs_investigation=True)
    else:
        raise CourtListenerUnavailable(f"unsupported lookup status {status}")
    return ResolutionResult()


def _disambiguate_clusters(
    citation: Citation, clusters: object
) -> list[dict[str, Any]]:
    """Apply FR-RES-003 using CourtListener's live cluster fields.

    The lookup response exposes ``case_name``/``case_name_full`` and
    ``date_filed``. A candidate must be an exact normalised-name match and
    have either the cited year or no conflicting date before it can be chosen.
    """

    if not citation.case_name or not isinstance(clusters, list):
        return []
    matches: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        names = (cluster.get("case_name"), cluster.get("case_name_full"))
        if not any(
            isinstance(name, str)
            and case_name_similarity(citation.case_name, name) == 1.0
            for name in names
        ):
            continue
        date_filed = cluster.get("date_filed")
        if (
            citation.year_hint
            and isinstance(date_filed, str)
            and not date_filed.startswith(str(citation.year_hint))
        ):
            continue
        matches.append(cluster)
    return matches


async def _write_pending_investigation(
    session: Any, citation: Citation, status: int, candidates: object | None = None
) -> None:
    evidence: dict[str, Any] = {"courtlistener_status": status}
    if isinstance(candidates, list):
        evidence["candidates"] = candidates
    await _write_finding(
        session,
        citation,
        CHECK_EXISTENCE,
        ExistenceVerdict.PENDING.value,
        None,
        [],
        evidence,
    )
    existing = await session.scalar(
        select(InvestigatorRun).where(InvestigatorRun.citation_id == citation.id)
    )
    if existing is None:
        session.add(InvestigatorRun(citation_id=citation.id, status="queued"))
        await session.flush()


async def _fetch_source_material(
    client: CourtListenerClient, cluster_id: str
) -> SourceMaterial | None:
    """Fetch remote source data without retaining a database connection."""
    cluster = {"id": int(cluster_id)}
    opinions = await client.fetch_cluster_opinions(cluster)
    paragraphs: list[tuple[str, str | None]] = []
    for opinion in opinions:
        text = _clean_opinion_text(opinion)
        part = _opinion_part(opinion.get("type"))
        paragraphs.extend((paragraph, part) for paragraph in _paragraph_texts(text))
    if not paragraphs:
        return None
    return SourceMaterial(
        cluster_id=cluster_id,
        case_name=None,
        court=None,
        paragraphs=paragraphs,
    )


async def _ensure_courtlistener_provenance(
    session: Any, source: Source, acquisition: SourceAcquisition
) -> None:
    """Attach the durable FR-PRV-001 record without changing source retrieval."""

    existing = await session.scalar(
        select(Provenance.id).where(Provenance.source_id == source.id)
    )
    if existing is not None:
        return
    retrieved_at = acquisition.retrieved_at or datetime.now(timezone.utc)
    acquisition.retrieved_at = retrieved_at
    provenance = Provenance(
        source_id=source.id,
        url=acquisition.source_url,
        retrieved_at=retrieved_at,
        method="courtlistener",
        sha256=hashlib.sha256((source.text or "").encode("utf-8")).hexdigest(),
        # ADR-009 records that the current Railway volume is not a durable
        # snapshot store. Leave this honest null rather than a local path.
        snapshot_ref=None,
        session_ref=None,
    )
    try:
        async with session.begin_nested():
            session.add(provenance)
            await session.flush()
    except IntegrityError:
        # A concurrent citation may have persisted the same source's record
        # just before this task acquired it. The unique source key makes that
        # an idempotent success instead of a failed source acquisition.
        if (
            await session.scalar(
                select(Provenance.id).where(Provenance.source_id == source.id)
            )
            is None
        ):
            raise


async def _persist_source_material(
    session: Any, material: SourceMaterial, acquisition: SourceAcquisition
) -> Source:
    external_id = f"courtlistener-cluster:{material.cluster_id}"
    source: Source
    if external_id:
        existing = await session.scalar(
            select(Source).where(Source.external_id == external_id)
        )
        if existing is not None:
            await _ensure_courtlistener_provenance(session, existing, acquisition)
            return existing
    source = Source(
        kind="opinion",
        case_name=material.case_name,
        court=material.court,
        external_id=external_id,
        text="\n\n".join(text for text, _ in material.paragraphs),
    )
    try:
        # Multiple citations can resolve to the same cluster concurrently.
        # The unique external ID is the cross-worker lock; use a savepoint so
        # the loser can reuse the committed source instead of failing its
        # otherwise independent citation task.
        async with session.begin_nested():
            session.add(source)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(Source).where(Source.external_id == external_id)
        )
        if existing is None:
            raise
        await _ensure_courtlistener_provenance(session, existing, acquisition)
        return existing
    for number, (text, opinion_part) in enumerate(material.paragraphs, start=1):
        session.add(
            SourceParagraph(
                source_id=source.id,
                para_no=number,
                opinion_part=opinion_part,
                text=text,
            )
        )
    await _ensure_courtlistener_provenance(session, source, acquisition)
    return source


async def process_source(ctx: dict[str, Any], cluster_id: str) -> None:
    """Acquire one CourtListener cluster once, with a bounded shared budget."""
    sessionmaker = get_sessionmaker()
    source_for_index: Source | None = None
    paragraphs_for_index: list[SourceParagraph] = []
    async with sessionmaker() as session:
        acquisition = await session.scalar(
            select(SourceAcquisition)
            .where(SourceAcquisition.cluster_id == cluster_id)
            .with_for_update()
        )
        if acquisition is None:
            return
        if acquisition.state in {"fetched", "unavailable"}:
            citation_ids = list(
                await session.scalars(
                    select(Citation.id).where(
                        Citation.source_acquisition_id == acquisition.id
                    )
                )
            )
            await session.commit()
        else:
            citation_ids = []
            if acquisition.state == "fetching":
                # Another independently queued citation already owns this
                # cluster's bounded fetch; it will fan out quote tasks.
                await session.commit()
                return
            acquisition.state = "fetching"
            acquisition.attempt_count += 1
            await session.commit()

    if citation_ids:
        for citation_id in citation_ids:
            await ctx["redis"].enqueue_job(
                "process_quote", str(citation_id), _job_id=f"quote:{citation_id}"
            )
            await ctx["redis"].enqueue_job(
                "process_proposition",
                str(citation_id),
                _job_id=f"proposition:{citation_id}",
            )
        return

    settings = get_settings()
    material: SourceMaterial | None = None
    failure_code: str | None = None
    if not settings.courtlistener_api_token:
        failure_code = "UPSTREAM_NOT_CONFIGURED"
    else:
        http_client = ctx.get("courtlistener_http")
        owns_client = http_client is None
        if http_client is None:
            http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
                limits=httpx.Limits(max_connections=6, max_keepalive_connections=6),
            )
        try:
            semaphore = ctx.get("source_fetch_semaphore")
            client = CourtListenerClient(
                settings.courtlistener_api_token,
                client=http_client,
                acquire_rate_limit=CourtListenerRateLimiter(get_redis()).acquire,
                max_retries=1,
            )
            if semaphore is None:
                material = await _fetch_source_material(client, cluster_id)
            else:
                async with semaphore:
                    material = await _fetch_source_material(client, cluster_id)
            if material is None:
                failure_code = "SOURCE_EMPTY"
        except CourtListenerUnavailable:
            failure_code = "UPSTREAM_UNAVAILABLE"
        except asyncio.CancelledError:
            raise
        except Exception:
            # A malformed or unexpectedly slow upstream response must not
            # leave this acquisition permanently in ``fetching``.  Preserve a
            # terminal, honest source state for the dependent quote claims.
            sentry_sdk.capture_exception()
            failure_code = "SOURCE_RETRIEVAL_FAILED"
        finally:
            if owns_client:
                await http_client.aclose()

    async with sessionmaker() as session:
        acquisition = await session.scalar(
            select(SourceAcquisition).where(SourceAcquisition.cluster_id == cluster_id)
        )
        if acquisition is None:
            return
        if material is not None:
            # Set this before provenance persistence so its timestamp is the
            # source retrieval timestamp, not a later post-commit surrogate.
            acquisition.retrieved_at = datetime.now(timezone.utc)
            source = await _persist_source_material(session, material, acquisition)
            acquisition.state = "fetched"
            acquisition.source_id = source.id
            acquisition.failure_code = None
            source_for_index = source
            paragraphs_for_index = list(
                await session.scalars(
                    select(SourceParagraph)
                    .where(SourceParagraph.source_id == source.id)
                    .order_by(SourceParagraph.para_no)
                )
            )
        else:
            acquisition.state = "unavailable"
            acquisition.failure_code = failure_code or "UPSTREAM_UNAVAILABLE"
        citation_ids = list(
            await session.scalars(
                select(Citation.id).where(
                    Citation.source_acquisition_id == acquisition.id
                )
            )
        )
        await session.commit()

    if source_for_index is not None:
        await _index_source_best_effort(ctx, source_for_index, paragraphs_for_index)

    for citation_id in citation_ids:
        await ctx["redis"].enqueue_job(
            "process_quote", str(citation_id), _job_id=f"quote:{citation_id}"
        )
        await ctx["redis"].enqueue_job(
            "process_proposition",
            str(citation_id),
            _job_id=f"proposition:{citation_id}",
        )


def _load_domain_policy(filename: str) -> tuple[str, ...]:
    config_path = Path(__file__).resolve().parents[3] / "config" / filename
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    domains = payload.get("domains", []) if isinstance(payload, dict) else []
    if not isinstance(domains, list) or any(
        not isinstance(item, str) for item in domains
    ):
        raise ValueError(f"invalid domain policy in {config_path}")
    return tuple(domains)


def _investigator_context(citation: Citation) -> InvestigatorCitationContext:
    # Unrecognised citations do not reliably expose a case name. The raw
    # citation is still useful as an honest discovery query, but it cannot
    # accidentally pass the stricter candidate matcher below.
    return InvestigatorCitationContext(
        case_name=citation.case_name or citation.raw_text,
        parties=(citation.raw_text,),
        court=citation.court_hint,
        # Several reporter-only citation forms (including NY Slip Op) carry a
        # decision year but do not currently populate the parser's year_hint.
        # Supplying that *citation-asserted* year does not relax FR-INV-005:
        # the extracted official decision must independently expose the same
        # year, in addition to passing the case-name threshold.  Keep this
        # generic rather than encoding a court-specific exception here, so
        # future configured resolvers receive the same corroboration input.
        year=citation.year_hint or _citation_asserted_year(citation.raw_text),
        official_search_domain=_official_search_domain(citation),
        direct_official_candidates=tuple(
            DirectOfficialCandidate(
                url=candidate.url,
                resolver_name=candidate.resolver_name,
            )
            for candidate in resolve_official_urls(
                citation.raw_text, _load_official_url_resolvers()
            )
        ),
    )


def _citation_asserted_year(citation_text: str) -> int | None:
    """Return one unambiguous four-digit year explicitly present in a citation."""

    years = {
        int(value)
        for value in re.findall(r"(?<!\d)((?:1[6-9]|20)\d{2})(?!\d)", citation_text)
    }
    return next(iter(years)) if len(years) == 1 else None


def _official_search_domain(citation: Citation) -> str | None:
    """Select only an explicitly configured official discovery host."""

    thresholds = load_investigator_thresholds()
    court_hint = (citation.court_hint or "").casefold()
    citation_text = citation.raw_text.casefold()
    for rule in thresholds.official_search_domains:
        if rule.court_hint and rule.court_hint.casefold() in court_hint:
            return rule.domain
        if rule.citation_marker and rule.citation_marker.casefold() in citation_text:
            return rule.domain
    return None


def _load_official_url_resolvers() -> tuple[OfficialUrlResolverRule, ...]:
    """Load auditable P3 citation-to-official-URL discovery rules."""

    config_path = (
        Path(__file__).resolve().parents[3] / "config" / "official_url_resolvers.yaml"
    )
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    raw_rules = payload.get("resolvers", []) if isinstance(payload, dict) else []
    if not isinstance(raw_rules, list):
        raise ValueError(
            f"invalid official URL resolver configuration in {config_path}"
        )
    rules: list[OfficialUrlResolverRule] = []
    for item in raw_rules:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str)
            for field in ("name", "citation_pattern", "url_template")
        ):
            raise ValueError(f"invalid official URL resolver rule in {config_path}")
        rules.append(
            OfficialUrlResolverRule(
                name=item["name"],
                citation_pattern=item["citation_pattern"],
                url_template=item["url_template"],
            )
        )
    return tuple(rules)


def _candidate_matches_investigation(
    candidate: InvestigatorCandidate, context: InvestigatorCitationContext
) -> bool:
    thresholds = load_investigator_thresholds()
    year = (
        int(context.year)
        if isinstance(context.year, str) and context.year.isdigit()
        else context.year
    )
    return match_candidate(
        MatchCandidate(
            case_name=candidate.case_name,
            court=candidate.court,
            decision_date=candidate.decision_date,
            docket_no=candidate.docket_no,
            opinion_text=candidate.opinion_text,
        ),
        MatchCitationContext(
            case_name=context.case_name,
            court=context.court,
            year=year,
            docket_no=context.docket_no,
        ),
        name_similarity_threshold=thresholds.name_similarity_threshold,
    )


def _decision_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _publish_investigator_started(
    citation_id: uuid.UUID, job_id: uuid.UUID, live_view_url: str | None
) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        run = await session.scalar(
            select(InvestigatorRun).where(InvestigatorRun.citation_id == citation_id)
        )
        if run is None:
            return
        run.live_view_url = live_view_url
        await session.commit()
    await publish_event(
        str(job_id),
        "investigator.started",
        {"citation_id": str(citation_id), "live_view_url": live_view_url},
    )


async def process_investigation(ctx: dict[str, Any], citation_id: str) -> None:
    """Run P3's bounded official-source fallback independently per citation."""

    sessionmaker = get_sessionmaker()
    citation_pk = uuid.UUID(citation_id)
    async with sessionmaker() as session:
        citation = await session.get(Citation, citation_pk)
        if citation is None:
            return
        run = await session.scalar(
            select(InvestigatorRun)
            .where(InvestigatorRun.citation_id == citation_pk)
            .with_for_update()
        )
        if run is None or run.status == "completed":
            return
        if run.status == "running":
            return
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        job_id = citation.job_id
        context = _investigator_context(citation)
        await session.commit()

    settings = get_settings()
    outcome = None
    unavailable_reason: str | None = None
    try:
        if not settings.browserbase_api_key:
            raise InvestigatorUnavailable("Browserbase is not configured")
        thresholds = load_investigator_thresholds()

        async def on_live_view(url: str | None) -> None:
            await _publish_investigator_started(citation_pk, job_id, url)

        with sentry_sdk.start_span(
            op="investigator.run", name="process_investigation"
        ) as span:
            span.set_tag("job_id", str(job_id))
            span.set_tag("citation_id", citation_id)
            semaphore = ctx.get("investigator_semaphore")
            kwargs = {
                "allowlist": _load_domain_policy("allowlist.yaml"),
                "denylist": _load_domain_policy("denylist.yaml"),
                "match_candidate": _candidate_matches_investigation,
                "on_live_view": on_live_view,
            }
            budget = InvestigationBudget(
                max_searches=thresholds.max_searches,
                max_fetches=thresholds.max_fetches,
                max_wall_time_seconds=thresholds.max_wall_time_seconds,
            )
            if semaphore is None:
                outcome = await run_investigation(
                    settings.browserbase_api_key, context, budget, **kwargs
                )
            else:
                async with semaphore:
                    outcome = await run_investigation(
                        settings.browserbase_api_key, context, budget, **kwargs
                    )
            if outcome is not None:
                span.set_data("investigator.searches", outcome.searches)
                span.set_data("investigator.fetches", outcome.fetches)
                span.set_tag("investigator.verdict", outcome.verdict.value)
    except InvestigatorUnavailable as error:
        unavailable_reason = str(error)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        sentry_sdk.capture_exception(error)
        unavailable_reason = "investigator failed before a reliable existence result"

    async with sessionmaker() as session:
        citation = await session.get(Citation, citation_pk)
        run = await session.scalar(
            select(InvestigatorRun).where(InvestigatorRun.citation_id == citation_pk)
        )
        if citation is None or run is None:
            return
        run.status = "completed"
        run.ended_at = datetime.now(timezone.utc)
        if outcome is not None:
            run.searches = outcome.searches
            run.fetches = outcome.fetches
            run.live_view_url = outcome.live_view_url or run.live_view_url
            run.outcome = {
                "verdict": outcome.verdict.value,
                "official_url": outcome.official_url,
                "discovery_method": outcome.discovery_method,
                "budget_exhausted": outcome.budget_exhausted,
                "secondary_mentions": [
                    {"url": item.url, "title": item.title, "snippet": item.snippet}
                    for item in outcome.secondary_mentions
                ],
                "errors": list(outcome.errors),
            }
        else:
            run.outcome = {"verdict": "UNVERIFIABLE", "reason": unavailable_reason}

        source: Source | None = None
        if (
            outcome is not None
            and outcome.verdict is InvestigationVerdict.VERIFIED_OFFICIAL
        ):
            try:
                source = await _persist_investigated_source(session, citation, outcome)
            except InvestigatorUnavailable as error:
                unavailable_reason = str(error)
                run.outcome = {"verdict": "UNVERIFIABLE", "reason": unavailable_reason}
        if source is not None:
            await _write_finding(
                session,
                citation,
                CHECK_EXISTENCE,
                ExistenceVerdict.VERIFIED_OFFICIAL.value,
                1.0,
                [],
                {"source_id": str(source.id), "provenance_url": outcome.official_url},
            )
        elif outcome is not None and unavailable_reason is None:
            notes = ["INVESTIGATION_COMPLETED"]
            evidence = {
                "investigator_run_id": str(run.id),
                "searches": outcome.searches,
                "fetches": outcome.fetches,
            }
            if outcome.secondary_mentions:
                evidence["secondary_mentions"] = run.outcome["secondary_mentions"]
            await _write_finding(
                session,
                citation,
                CHECK_EXISTENCE,
                outcome.verdict.value,
                None,
                notes,
                evidence,
            )
        else:
            # An unavailable investigator is uncertainty, not proof that a
            # case does not exist. This preserves OVERVIEW's central safety
            # promise even when Browserbase is misconfigured or unavailable.
            await _write_finding(
                session,
                citation,
                CHECK_EXISTENCE,
                ExistenceVerdict.UNVERIFIABLE.value,
                None,
                ["INVESTIGATOR_UNAVAILABLE"],
                {"investigator_run_id": str(run.id)},
            )

        if source is not None:
            await _publish_citation_findings(session, citation, ctx.get("elastic"))
            await session.commit()
            await _index_source_best_effort(
                ctx,
                source,
                list(
                    await session.scalars(
                        select(SourceParagraph).where(
                            SourceParagraph.source_id == source.id
                        )
                    )
                ),
            )
            await ctx["redis"].enqueue_job(
                "process_quote", citation_id, _job_id=f"quote:{citation_id}"
            )
            await ctx["redis"].enqueue_job(
                "process_proposition", citation_id, _job_id=f"proposition:{citation_id}"
            )
            await publish_event(
                str(job_id),
                "investigator.completed",
                {"citation_id": citation_id, "outcome": run.outcome},
            )
            return

        await _write_quote_unavailable(session, citation)
        await _write_proposition_unavailable(session, citation, "SOURCE_UNAVAILABLE")
        await _publish_citation_findings(session, citation, ctx.get("elastic"))
        await _maybe_finish_job(session, citation.job_id)
        await session.commit()
    await publish_event(
        str(job_id),
        "investigator.completed",
        {"citation_id": citation_id, "outcome": run.outcome},
    )


async def _persist_investigated_source(
    session: Any, citation: Citation, outcome: Any
) -> Source:
    """Persist a per-citation investigator result and its provenance."""

    candidate = outcome.official_match
    if candidate is None or not candidate.opinion_text or not outcome.official_url:
        raise InvestigatorUnavailable(
            "official match did not include retrievable opinion text"
        )
    source = Source(
        kind="opinion",
        case_name=candidate.case_name,
        court=candidate.court,
        decision_date=_decision_datetime(candidate.decision_date),
        docket_no=candidate.docket_no,
        external_id=f"investigator:{citation.id}",
        text=candidate.opinion_text,
    )
    session.add(source)
    await session.flush()
    for number, paragraph in enumerate(
        _paragraph_texts(candidate.opinion_text), start=1
    ):
        session.add(
            SourceParagraph(
                source_id=source.id,
                para_no=number,
                opinion_part="unknown",
                text=paragraph,
            )
        )
    acquisition = SourceAcquisition(
        cluster_id=f"investigator:{citation.id}",
        state="fetched",
        source_id=source.id,
        source_url=outcome.official_url,
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(acquisition)
    await session.flush()
    citation.source_acquisition_id = acquisition.id
    session.add(
        Provenance(
            source_id=source.id,
            url=outcome.official_url,
            retrieved_at=acquisition.retrieved_at,
            method="stagehand_extract",
            sha256=hashlib.sha256(candidate.opinion_text.encode("utf-8")).hexdigest(),
            snapshot_ref=None,
            session_ref=outcome.session_ref,
        )
    )
    return source


async def _index_source_best_effort(
    ctx: dict[str, Any], source: Source, paragraphs: list[SourceParagraph]
) -> None:
    """Mirror durable source paragraphs to Elastic without a new failure mode."""

    elastic = ctx.get("elastic")
    if elastic is None or not paragraphs:
        return
    vectors: dict[int, list[float]] = {}
    api_key = get_settings().openai_api_key
    if api_key:
        try:
            for start in range(0, len(paragraphs), 64):
                batch = paragraphs[start : start + 64]
                semaphore = ctx.get("embedding_semaphore")
                if semaphore is None:
                    embedded = await embed_texts(
                        api_key,
                        [item.text for item in batch],
                        client=ctx.get("openai_http"),
                    )
                else:
                    async with semaphore:
                        embedded = await embed_texts(
                            api_key,
                            [item.text for item in batch],
                            client=ctx.get("openai_http"),
                        )
                vectors.update(
                    {
                        item.para_no: vector
                        for item, vector in zip(batch, embedded, strict=True)
                    }
                )
        except Exception:  # Elastic can still retain BM25-only documents.
            vectors = {}
    try:
        await index_source_paragraphs(
            elastic, source, paragraphs, embeddings_by_para=vectors
        )
    except ElasticUnavailable:
        return


async def process_quote(ctx: dict[str, Any], citation_id: str) -> None:
    """Check a quote only after its durable source stage is terminal."""
    sessionmaker = get_sessionmaker()
    quote_results: list[tuple[str, float | None, list[str], dict[str, Any]]] = []
    source_unavailable = False
    quote_work: tuple[Citation, Source, list[Claim], list[SourceParagraph]] | None = (
        None
    )
    async with sessionmaker() as session:
        citation = await session.get(Citation, uuid.UUID(citation_id))
        if citation is None or citation.source_acquisition_id is None:
            return
        job_id = citation.job_id
        acquisition = await session.get(
            SourceAcquisition, citation.source_acquisition_id
        )
        if acquisition is None or acquisition.state not in {"fetched", "unavailable"}:
            return
        if acquisition.state == "fetched" and acquisition.source_id:
            source = await session.get(Source, acquisition.source_id)
            claims = list(
                await session.scalars(
                    select(Claim).where(Claim.citation_id == citation.id)
                )
            )
            if source is not None:
                paragraphs = list(
                    await session.scalars(
                        select(SourceParagraph)
                        .where(SourceParagraph.source_id == source.id)
                        .order_by(SourceParagraph.para_no)
                    )
                )
                # This reads all required evidence before releasing the
                # connection.  Deterministic alignment and any optional
                # embedding request below therefore never hold a DB session.
                quote_work = (citation, source, claims, paragraphs)
            else:
                source_unavailable = True
        else:
            source_unavailable = True

    if quote_work is not None:
        try:
            quote_results = await _quote_check(
                *quote_work,
                embedding_client=ctx.get("openai_http"),
                embedding_semaphore=ctx.get("embedding_semaphore"),
                elastic_client=ctx.get("elastic"),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A single malformed source paragraph or alignment edge case is
            # not allowed to strand the complete review.  SOURCE_UNAVAILABLE
            # is the approved neutral P1 quote state when no usable evidence
            # can be produced (NFR-REL-001).
            sentry_sdk.capture_exception()
            source_unavailable = True

    async with sessionmaker() as session:
        citation = await session.get(Citation, uuid.UUID(citation_id))
        if citation is None:
            return
        if source_unavailable:
            await _write_quote_unavailable(session, citation)
        for verdict, confidence, notes, evidence in quote_results:
            await _write_finding(
                session, citation, CHECK_QUOTE, verdict, confidence, notes, evidence
            )
        await session.commit()
        await _publish_citation_findings(session, citation, ctx.get("elastic"))
        await _maybe_finish_job(session, job_id)


async def process_proposition(ctx: dict[str, Any], citation_id: str) -> None:
    """Retrieve evidence and validate one bounded proposition judgement."""

    sessionmaker = get_sessionmaker()
    work: tuple[Citation, Source, str] | None = None
    proposition_for_signal: str | None = None
    async with sessionmaker() as session:
        citation = await session.get(Citation, uuid.UUID(citation_id))
        if citation is None:
            return
        claim = await session.scalar(
            select(Claim).where(Claim.citation_id == citation.id)
        )
        proposition_for_signal = (
            claim.proposition_text if claim is not None and claim.proposition_text else None
        )
        acquisition = (
            await session.get(SourceAcquisition, citation.source_acquisition_id)
            if citation.source_acquisition_id
            else None
        )
        if claim is None or not claim.proposition_text:
            await _write_proposition_unavailable(
                session, citation, "PROPOSITION_UNAVAILABLE"
            )
        elif (
            acquisition is None
            or acquisition.state != "fetched"
            or not acquisition.source_id
        ):
            await _write_proposition_unavailable(
                session, citation, "SOURCE_UNAVAILABLE"
            )
        else:
            source = await session.get(Source, acquisition.source_id)
            if source is None:
                await _write_proposition_unavailable(
                    session, citation, "SOURCE_UNAVAILABLE"
                )
            else:
                work = (citation, source, claim.proposition_text)
        await session.commit()
        await _publish_citation_findings(session, citation, ctx.get("elastic"))
        await _maybe_finish_job(session, citation.job_id)

    if work is None:
        if proposition_for_signal is not None:
            await _score_proposition_signal_best_effort(
                ctx, citation.job_id, citation.id, proposition_for_signal
            )
        return
    citation, source, proposition = work
    result = None
    retrieved: list[RetrievedParagraph] = []
    unavailable_reason: str | None = None
    try:
        elastic = ctx.get("elastic")
        if elastic is None:
            raise ElasticUnavailable("Elastic is not configured")
        with sentry_sdk.start_span(
            op="proposition.retrieval", name="retrieve_paragraphs"
        ):
            retrieved = await retrieve_paragraphs(
                elastic,
                str(source.id),
                proposition,
                load_proposition_thresholds().top_k_paragraphs,
                embed_query=lambda text: _embed_proposition(ctx, text),
                rerank=ctx.get("elastic_reranker"),
            )
        if not retrieved:
            unavailable_reason = "NO_RETRIEVED_EVIDENCE"
        elif not get_settings().openai_api_key:
            unavailable_reason = "JUDGE_NOT_CONFIGURED"
        else:
            judge_paragraphs = [
                JudgeParagraph(item.para_id, item.opinion_part, item.text)
                for item in retrieved
            ]
            # Raw HTTP keeps the OpenAI key server-side, so record the AI
            # boundary explicitly for Sentry's tracing/AI-monitoring view.
            with sentry_sdk.start_span(
                op="ai.generate_text", name="openai.proposition_judge"
            ) as span:
                span.set_tag("gen_ai.system", "openai")
                span.set_tag("gen_ai.operation.name", "proposition_judge")

                def capture_usage(
                    model: str, prompt_tokens: int | None, completion_tokens: int | None
                ) -> None:
                    span.set_tag("gen_ai.request.model", model)
                    if prompt_tokens is not None:
                        span.set_data("gen_ai.usage.input_tokens", prompt_tokens)
                    if completion_tokens is not None:
                        span.set_data("gen_ai.usage.output_tokens", completion_tokens)

                raw = await call_judge(
                    get_settings().openai_api_key,
                    proposition,
                    judge_paragraphs,
                    ctx.get("openai_http"),
                    on_usage=capture_usage,
                )
            result = evaluate_proposition(raw, judge_paragraphs)
            if result.verdict is PropositionVerdict.UNVERIFIABLE:
                sentry_logger.warning(
                    "Proposition judge response did not pass deterministic validation",
                    attributes={
                        "pincite.citation_id": citation_id,
                        "pincite.validation_reason": result.reason or "UNVERIFIABLE",
                    },
                )
    except (ElasticUnavailable, EmbeddingUnavailable, JudgeUnavailable):
        unavailable_reason = "UPSTREAM_UNAVAILABLE"
    except asyncio.CancelledError:
        raise
    except Exception:
        sentry_sdk.capture_exception()
        unavailable_reason = "UPSTREAM_UNAVAILABLE"

    async with sessionmaker() as session:
        persisted = await session.get(Citation, citation.id)
        if persisted is None:
            return
        if result is None:
            await _write_proposition_unavailable(
                session, persisted, unavailable_reason or "UPSTREAM_UNAVAILABLE"
            )
        else:
            evidence = {
                "source_id": str(source.id),
                "paragraphs": [
                    {
                        "para_id": item.para_id,
                        "opinion_part": item.opinion_part,
                        "page": item.page,
                    }
                    for item in retrieved
                    if item.para_id in result.cited_paragraph_ids
                ],
                "cited_paragraph_ids": list(result.cited_paragraph_ids),
            }
            rationale = (
                result.rationale
                or "PinCite could not validate the judge response against the retrieved evidence."
            )
            await _write_finding(
                session,
                persisted,
                CHECK_PROPOSITION,
                result.verdict.value,
                result.confidence,
                list(result.notes) + ([result.reason] if result.reason else []),
                evidence,
                rationale=rationale,
            )
        await session.commit()
        await _publish_citation_findings(session, persisted, ctx.get("elastic"))
        await _maybe_finish_job(session, persisted.job_id)

    # This follows the completed finding transaction. It has no reference to
    # the validated judge result or the finding writer, so it cannot change a
    # verdict, confidence, notes, or terminal job state (CON-SIG-001 / INV-2).
    if proposition:
        await _score_proposition_signal_best_effort(
            ctx, citation.job_id, citation.id, proposition
        )


async def _score_proposition_signal_best_effort(
    ctx: dict[str, Any],
    job_id: uuid.UUID,
    citation_id: uuid.UUID,
    proposition: str,
) -> None:
    """Persist one post-verdict hallucination signal if GPTZero supports it."""

    api_key = get_settings().gptzero_api_key
    if not api_key:
        return
    try:
        scored = await score_hallucination_result(
            api_key, proposition, client=ctx.get("gptzero_http")
        )
        if scored is None:
            return
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await _write_signal(
                session,
                job_id=job_id,
                citation_id=citation_id,
                section_ref=None,
                provider="gptzero",
                kind="hallucination",
                score=scored.score,
                raw=scored.raw,
            )
            await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        # A signal outage is deliberately not an upstream verdict outage.
        sentry_sdk.capture_exception()


async def _embed_proposition(ctx: dict[str, Any], text: str) -> list[float] | None:
    api_key = get_settings().openai_api_key
    if not api_key:
        return None
    semaphore = ctx.get("embedding_semaphore")
    if semaphore is None:
        vectors = await embed_texts(api_key, [text], client=ctx.get("openai_http"))
    else:
        async with semaphore:
            vectors = await embed_texts(api_key, [text], client=ctx.get("openai_http"))
    return vectors[0] if vectors else None


async def _quote_check(
    citation: Citation,
    source: Source,
    claims: list[Claim],
    paragraphs: list[SourceParagraph],
    *,
    embedding_client: httpx.AsyncClient | None = None,
    embedding_semaphore: asyncio.Semaphore | None = None,
    semantic_check_enabled: bool = True,
    elastic_client: Any | None = None,
) -> list[tuple[str, float | None, list[str], dict[str, Any]]]:
    findings: list[tuple[str, float | None, list[str], dict[str, Any]]] = []
    for claim in claims:
        if not claim.quote_text:
            continue
        # CourtListener opinions can contain thousands of paragraphs.  A
        # high-similarity alignment necessarily shares several quote terms;
        # rank by that signal first, then run the exact deterministic checker
        # on a bounded local window.  This is retrieval, not a verdict—the
        # checker and its evidence remain entirely deterministic and local.
        verification_paragraphs = _quote_verification_candidates(
            claim.quote_text, paragraphs
        )
        if elastic_client is not None:
            try:
                elastic_candidates = await retrieve_quote_candidates(
                    elastic_client, str(source.id), claim.quote_text
                )
                by_para_no = {item.para_no: item for item in paragraphs}
                verification_paragraphs = [
                    by_para_no[item.para_no]
                    for item in elastic_candidates
                    if item.para_no in by_para_no
                ] or verification_paragraphs
            except ElasticUnavailable:
                # ADR-010: preserve P1's deterministic local source when the
                # new candidate index is unavailable.
                pass
        verdict_paragraphs = [
            VerdictParagraph(str(item.id), item.text, item.page, item.opinion_part)
            for item in verification_paragraphs
        ]
        result = await asyncio.to_thread(
            check_quote,
            claim.quote_text,
            verdict_paragraphs,
            pinpoint_page=_pinpoint_page(citation.pinpoint),
        )
        sentry_logger.info(
            "Deterministic quote alignment completed",
            attributes={
                "pincite.quote.verdict": result.verdict.value,
                "pincite.quote.similarity": result.similarity,
                "pincite.quote.confidence": result.confidence,
                **(
                    {"pincite.citation_id": str(citation.id)}
                    if getattr(citation, "id", None) is not None
                    else {}
                ),
            },
        )
        # Embeddings are an optional refinement, never a P1 completion
        # dependency. The terminal deterministic finding remains honest about
        # a semantic question that has not yet been run.
        if (
            semantic_check_enabled
            and result.semantic_check_status.value == "NOT_CONFIGURED"
            and get_settings().openai_api_key
        ):
            candidates = _semantic_candidates(claim.quote_text, verification_paragraphs)
            try:
                if embedding_semaphore is None:
                    scores = await cosine_scores(
                        get_settings().openai_api_key,
                        claim.quote_text,
                        candidates,
                        client=embedding_client,
                    )
                else:
                    async with embedding_semaphore:
                        scores = await cosine_scores(
                            get_settings().openai_api_key,
                            claim.quote_text,
                            candidates,
                            client=embedding_client,
                        )
                result = await asyncio.to_thread(
                    check_quote,
                    claim.quote_text,
                    verdict_paragraphs,
                    pinpoint_page=_pinpoint_page(citation.pinpoint),
                    semantic_scores=scores,
                )
            except EmbeddingUnavailable:
                pass
        evidence: dict[str, Any] = {
            "source_id": str(source.id),
            "diff": [
                {
                    "op": item.operation,
                    "quote_tokens": list(item.quote_tokens),
                    "source_tokens": list(item.source_tokens),
                }
                for item in result.diff
            ],
        }
        if result.evidence:
            evidence["paragraphs"] = [
                {"para_id": result.evidence.paragraph_id, "page": result.evidence.page}
            ]
        if result.closest_passage:
            evidence["closest_actual_language"] = {
                "paragraph_id": result.closest_passage.paragraph_id,
                "text": result.closest_passage.text,
            }
        opinion_notes = (
            _opinion_part_note(result.evidence.paragraph_id, verification_paragraphs)
            if result.evidence
            else []
        )
        findings.append(
            (
                result.verdict.value,
                result.confidence,
                [
                    *result.notes,
                    *opinion_notes,
                    f"SEMANTIC_CHECK_{result.semantic_check_status.value}",
                ],
                evidence,
            )
        )
    return findings


def _quote_verification_candidates(
    quote: str, paragraphs: list[SourceParagraph]
) -> list[SourceParagraph]:
    """Select plausible local passages before word-level alignment.

    A 24-passage cap bounds worst-case work on long opinions.  Every retained
    passage is still original persisted opinion text; the quote checker itself
    remains the sole verdict authority.
    """
    if len(paragraphs) <= 24:
        return paragraphs
    terms = set(re.findall(r"[a-z0-9]+", quote.casefold()))
    ranked = sorted(
        enumerate(paragraphs),
        key=lambda indexed: (
            len(terms & set(re.findall(r"[a-z0-9]+", indexed[1].text.casefold()))),
            -indexed[0],
        ),
        reverse=True,
    )
    return [paragraph for _, paragraph in ranked[:24]]


def _semantic_candidates(
    quote: str, paragraphs: list[SourceParagraph]
) -> list[tuple[str, str]]:
    """Bound request cost while preferring paragraphs with lexical signal."""
    terms = set(re.findall(r"[a-z0-9]+", quote.casefold()))
    ranked = sorted(
        paragraphs,
        key=lambda item: len(
            terms & set(re.findall(r"[a-z0-9]+", item.text.casefold()))
        ),
        reverse=True,
    )
    return [(str(item.id), item.text) for item in ranked[:32]]


def _opinion_part_note(
    paragraph_id: str, paragraphs: list[SourceParagraph]
) -> list[str]:
    """Flag quote evidence from a non-majority or indeterminate opinion."""

    paragraph = next(
        (item for item in paragraphs if str(item.id) == paragraph_id), None
    )
    if (
        paragraph is None
        or paragraph.opinion_part == "unknown"
        or paragraph.opinion_part is None
    ):
        return ["OPINION_PART_UNKNOWN"]
    if paragraph.opinion_part == "dissent":
        return ["QUOTED_FROM_DISSENT"]
    if paragraph.opinion_part == "concurrence":
        return ["QUOTED_FROM_CONCURRENCE"]
    return []


def _pinpoint_page(pinpoint: str | None) -> int | None:
    """Use the first numeric pinpoint for P1's informational page note."""
    if not pinpoint:
        return None
    match = re.search(r"\d+", pinpoint)
    return int(match.group()) if match else None


async def _write_quote_unavailable(session: Any, citation: Citation) -> None:
    claims = list(
        await session.scalars(select(Claim).where(Claim.citation_id == citation.id))
    )
    if any(claim.quote_text for claim in claims):
        await _write_finding(
            session, citation, CHECK_QUOTE, "SOURCE_UNAVAILABLE", 0.0, [], {}
        )


async def _write_proposition_unavailable(
    session: Any, citation: Citation, reason: str
) -> None:
    """Every citation gets a terminal proposition finding, even without a source."""

    await _write_finding(
        session,
        citation,
        CHECK_PROPOSITION,
        PropositionVerdict.UNVERIFIABLE.value,
        0.0,
        [reason],
        {},
        rationale="PinCite could not verify this proposition from a retrieved court opinion.",
    )


async def _write_finding(
    session: Any,
    citation: Citation,
    check: str,
    verdict: str,
    confidence: float | None,
    notes: list[str],
    evidence: dict[str, Any],
    *,
    rationale: str | None = None,
) -> Finding:
    existing = await session.scalar(
        select(Finding).where(
            Finding.citation_id == citation.id, Finding.check == check
        )
    )
    if existing is None:
        existing = Finding(
            citation_id=citation.id,
            check=check,
            verdict=verdict,
            confidence=confidence,
            notes=notes,
            evidence=evidence,
            rationale=rationale,
        )
        session.add(existing)
    else:
        existing.verdict, existing.confidence, existing.notes, existing.evidence = (
            verdict,
            confidence,
            notes,
            evidence,
        )
        existing.rationale = rationale
    await session.flush()
    return existing


async def _write_signal(
    session: Any,
    *,
    job_id: uuid.UUID,
    citation_id: uuid.UUID | None,
    section_ref: str | None,
    provider: str,
    kind: str,
    score: float,
    raw: dict[str, Any],
) -> Signal:
    """Idempotently retain one provider score outside the Finding table."""

    if (citation_id is None) == (section_ref is None):
        raise ValueError("a signal must identify exactly one citation or section")
    criteria = [
        Signal.job_id == job_id,
        Signal.provider == provider,
        Signal.kind == kind,
    ]
    if citation_id is not None:
        criteria.append(Signal.citation_id == citation_id)
    else:
        criteria.append(Signal.section_ref == section_ref)
    existing = await session.scalar(select(Signal).where(*criteria))
    if existing is None:
        existing = Signal(
            job_id=job_id,
            citation_id=citation_id,
            section_ref=section_ref,
            provider=provider,
            kind=kind,
            score=score,
            raw=raw,
        )
        session.add(existing)
    else:
        existing.score = score
        existing.raw = raw
        existing.created_at = datetime.now(timezone.utc)
    await session.flush()
    return existing


async def _publish_citation_findings(
    session: Any, citation: Citation, elastic_client: Any | None = None
) -> None:
    findings = list(
        await session.scalars(select(Finding).where(Finding.citation_id == citation.id))
    )
    for finding in findings:
        if elastic_client is not None:
            try:
                await index_finding(elastic_client, finding, citation)
            except ElasticUnavailable:
                pass
        await publish_event(
            str(citation.job_id),
            "finding.created",
            {
                "finding_id": str(finding.id),
                "citation_id": str(citation.id),
                "check": finding.check,
                "verdict": finding.verdict,
                "confidence": finding.confidence,
                "rationale": finding.rationale,
                "created_at": finding.created_at.isoformat(),
            },
        )


async def _maybe_finish_job(session: Any, job_id: uuid.UUID) -> None:
    total = await session.scalar(
        select(func.count(Citation.id)).where(Citation.job_id == job_id)
    )
    existence_complete = await session.scalar(
        select(func.count(Finding.id))
        .join(Citation, Finding.citation_id == Citation.id)
        .where(Citation.job_id == job_id, Finding.check == CHECK_EXISTENCE)
    )
    quote_required = await session.scalar(
        select(func.count(func.distinct(Citation.id)))
        .join(Claim, Claim.citation_id == Citation.id)
        .where(Citation.job_id == job_id, Claim.quote_text.is_not(None))
    )
    quote_complete = await session.scalar(
        select(func.count(func.distinct(Finding.citation_id)))
        .join(Citation, Finding.citation_id == Citation.id)
        .where(Citation.job_id == job_id, Finding.check == CHECK_QUOTE)
    )
    proposition_complete = await session.scalar(
        select(func.count(func.distinct(Finding.citation_id)))
        .join(Citation, Finding.citation_id == Citation.id)
        .where(Citation.job_id == job_id, Finding.check == CHECK_PROPOSITION)
    )
    investigation_required = await session.scalar(
        select(func.count(InvestigatorRun.id))
        .join(Citation, InvestigatorRun.citation_id == Citation.id)
        .where(Citation.job_id == job_id)
    )
    investigation_complete = await session.scalar(
        select(func.count(InvestigatorRun.id))
        .join(Citation, InvestigatorRun.citation_id == Citation.id)
        .where(Citation.job_id == job_id, InvestigatorRun.status == "completed")
    )
    if (
        total
        and total == existence_complete
        and quote_required == quote_complete
        and total == proposition_complete
        and investigation_required == investigation_complete
    ):
        job = await session.get(Job, job_id)
        if job and job.status != JobStatus.COMPLETED.value:
            await _complete_job(session, job)
            await session.commit()
            await publish_event(
                str(job_id),
                "job.completed",
                {"summary": await _job_summary(session, job_id)},
            )


async def _job_summary(session: Any, job_id: uuid.UUID) -> dict[str, int]:
    rows = await session.execute(
        select(Finding.verdict, func.count(Finding.id))
        .join(Citation, Finding.citation_id == Citation.id)
        .where(Citation.job_id == job_id)
        .group_by(Finding.verdict)
    )
    return {str(verdict): int(count) for verdict, count in rows.all()}


async def _complete_job(session: Any, job: Job) -> None:
    job.status = JobStatus.COMPLETED.value
    job.completed_at = datetime.now(timezone.utc)
