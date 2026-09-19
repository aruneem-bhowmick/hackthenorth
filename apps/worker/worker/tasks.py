"""P1 asynchronous pipeline orchestration.

The job task only ingests and persists brief facts.  Each citation then runs as
its own retry-safe arq task, so a slow CourtListener response cannot block
otherwise independent citations (FR-SYS-002).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import sentry_sdk
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pipeline import extract_citations, extract_pdf
from pipeline.ingestion import IngestionError
from pipeline.models import AntecedentState
from verdicts import SourceParagraph as VerdictParagraph
from verdicts import check_quote

from worker.config import get_settings
from worker.courtlistener import (
    MAX_CITATIONS_PER_REQUEST,
    MAX_TEXT_CHARS_PER_REQUEST,
    CitationLookup,
    CourtListenerClient,
    CourtListenerUnavailable,
)
from worker.embeddings import EmbeddingUnavailable, cosine_scores
from worker.db import BriefPage, Claim, Citation, Finding, Job, JobStatus, LookupCache, Source, SourceParagraph, get_sessionmaker
from worker.rate_limit import COURTLISTENER_VALID_CITATIONS_PER_MINUTE, CourtListenerRateLimiter
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
    value = opinion.get("html_with_citations") or opinion.get("plain_text") or opinion.get("html") or ""
    if not isinstance(value, str):
        return ""
    if "<" not in value:
        return value.strip()
    parser = _TextExtractor()
    parser.feed(value)
    return parser.text().strip()


def _paragraph_texts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


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
    transaction = sentry_sdk.continue_trace(headers, op="queue.task", name="process_job")
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
                raise IngestionError("UNSUPPORTED_FILE", "The uploaded PDF could not be found.")
            document = extract_pdf(pdf_path)
            job.page_count = document.page_count
            await _persist_brief_pages(session, job.id, document)
            extracted = extract_citations(document)
            persisted = await _persist_citations(session, job.id, extracted)
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
            await publish_event(job_id, "job.failed", {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # noqa: BLE001 -- no unexpected failure may strand a job
            await session.rollback()
            job.status = JobStatus.FAILED.value
            await session.commit()
            await publish_event(job_id, "job.failed", {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "The job could not be processed."}})
            raise exc


async def _persist_citations(session: Any, job_id: uuid.UUID, extracted: Any) -> list[Citation]:
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
        if item.quote:
            session.add(
                Claim(
                    citation_id=record.id,
                    quote_text=item.quote.text,
                    quote_start=item.quote.original_span.start,
                    quote_end=item.quote.original_span.end,
                    quote_processing_start=item.quote.processing_span.start,
                    quote_processing_end=item.quote.processing_span.end,
                )
            )
    return records


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
        if citation.kind == "full" and citation.resolution_state != AntecedentState.UNRESOLVED.value
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
    pending = [citation for normalized, citation in unique_targets.items() if normalized not in cached]
    if not pending:
        return

    limiter = CourtListenerRateLimiter(get_redis())
    client = CourtListenerClient(settings.courtlistener_api_token, acquire_rate_limit=limiter.acquire)
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
            len(current) == min(MAX_CITATIONS_PER_REQUEST, COURTLISTENER_VALID_CITATIONS_PER_MINUTE)
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


def _batch_text(citations: list[Citation]) -> tuple[str, dict[uuid.UUID, tuple[int, int]]]:
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
    citations: list[Citation], spans: dict[uuid.UUID, tuple[int, int]], lookups: list[CitationLookup]
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
    existing = await session.scalar(select(BriefPage.id).where(BriefPage.job_id == job_id).limit(1))
    if existing is not None:
        return
    session.add_all([BriefPage(job_id=job_id, page=item.page, text=item.raw_text) for item in document.pages])
    # Persist extracted brief text before citation fan-out makes it reviewable.
    await session.flush()


async def process_citation(ctx: dict[str, Any], citation_id: str) -> None:
    """Resolve and quote-check one citation, then publish its completed findings."""

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        citation = await session.get(Citation, uuid.UUID(citation_id))
        if citation is None:
            return
        citation_pk = citation.id
        job_pk = citation.job_id
        with sentry_sdk.start_span(op="citation.verify", name="process_citation") as span:
            span.set_tag("job_id", str(citation.job_id))
            span.set_tag("citation_id", citation_id)
            try:
                await _resolve_citation(session, citation)
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
                    check="existence",
                    verdict="UNVERIFIABLE",
                    confidence=None,
                    notes=["UPSTREAM_UNAVAILABLE"],
                    evidence={},
                )
                await session.commit()
            await _publish_citation_findings(session, citation)
            await _maybe_finish_job(session, job_pk)


async def _resolve_citation(session: Any, citation: Citation) -> None:
    if citation.resolution_state == AntecedentState.UNRESOLVED.value:
        await _write_finding(
            session, citation, "existence", "UNVERIFIABLE", None, ["UNRESOLVED_REFERENCE"], {}
        )
        return
    target = citation
    if citation.antecedent_id:
        antecedent = await session.get(Citation, citation.antecedent_id)
        if antecedent is None:
            await _write_finding(
                session, citation, "existence", "UNVERIFIABLE", None, ["UNRESOLVED_REFERENCE"], {}
            )
            return
        target = antecedent

    cache = await session.scalar(select(LookupCache).where(LookupCache.normalized_citation == target.normalized))
    lookup_payload: dict[str, Any]
    source: Source | None = await session.get(Source, cache.source_id) if cache and cache.source_id else None
    if cache is not None:
        lookup_payload = cache.response
    else:
        settings = get_settings()
        if not settings.courtlistener_api_token:
            raise CourtListenerUnavailable("CourtListener is not configured")
        limiter = CourtListenerRateLimiter(get_redis())
        client = CourtListenerClient(settings.courtlistener_api_token, acquire_rate_limit=limiter.acquire)
        lookups = await client.lookup_text(target.raw_text, expected_citations=1)
        if not lookups:
            citation.resolution_state = "unrecognized"
            await _write_finding(
                session, citation, "existence", "UNVERIFIABLE", None, ["UNRECOGNIZED_REFERENCE"], {}
            )
            await _write_quote_unavailable(session, citation)
            return
        lookup = lookups[0]
        lookup_payload = _lookup_payload(lookup)
        cache = LookupCache(normalized_citation=target.normalized, status=str(lookup.status), response=lookup_payload)
        session.add(cache)
        await session.flush()

    status = int(lookup_payload["status"])
    if status == 200:
        citation.resolution_state = "resolved"
        await _write_finding(
            session,
            citation,
            "existence",
            "VERIFIED",
            1.0,
            [],
            {"source_id": str(source.id)} if source else {},
        )
        if citation.antecedent_id:
            citation.resolution_state = "resolved_via_antecedent"
        claims = list(await session.scalars(select(Claim).where(Claim.citation_id == citation.id)))
        if not claims:
            return
        # The P1 demo's essential evidence is the source for an attached quote.
        # Do not retrieve an entire opinion for citation-only occurrences: its
        # lookup result already supports the existence finding. This is the
        # scoped lazy-source exception approved for NFR-PERF-002.
        if source is None and lookup_payload.get("clusters"):
            settings = get_settings()
            if settings.courtlistener_api_token:
                client = CourtListenerClient(settings.courtlistener_api_token)
                try:
                    source = await _store_source(session, client, lookup_payload["clusters"][0])
                except CourtListenerUnavailable:
                    source = None
                if source is not None:
                    cache.source_id = source.id
        if source is not None:
            await _quote_check(session, citation, source, claims)
        else:
            await _write_quote_unavailable(session, citation)
    elif status == 404:
        citation.resolution_state = "not_in_database"
        await _write_finding(session, citation, "existence", "NOT_IN_DATABASE", 1.0, [], {})
        await _write_quote_unavailable(session, citation)
    elif status == 300:
        citation.resolution_state = "ambiguous"
        await _write_finding(
            session,
            citation,
            "existence",
            "AMBIGUOUS",
            None,
            [],
            {"candidates": lookup_payload.get("clusters", [])},
        )
        await _write_quote_unavailable(session, citation)
    elif status == 400:
        citation.resolution_state = "unrecognized"
        await _write_finding(
            session, citation, "existence", "UNVERIFIABLE", None, ["UNRECOGNIZED_REFERENCE"], {}
        )
        await _write_quote_unavailable(session, citation)
    else:
        raise CourtListenerUnavailable(f"unsupported lookup status {status}")


async def _store_source(session: Any, client: CourtListenerClient, cluster: dict[str, Any]) -> Source | None:
    cluster_id = cluster.get("id")
    external_id = f"courtlistener-cluster:{cluster_id}" if cluster_id else None
    if external_id:
        existing = await session.scalar(select(Source).where(Source.external_id == external_id))
        if existing is not None:
            return existing
    opinions = await client.fetch_cluster_opinions(cluster)
    paragraphs: list[tuple[str, str | None]] = []
    for opinion in opinions:
        text = _clean_opinion_text(opinion)
        part = opinion.get("type") if isinstance(opinion.get("type"), str) else None
        paragraphs.extend((paragraph, part) for paragraph in _paragraph_texts(text))
    if not paragraphs:
        return None
    source = Source(
        kind="opinion",
        case_name=str(cluster.get("case_name") or "") or None,
        court=str(cluster.get("court") or "") or None,
        external_id=external_id,
        text="\n\n".join(text for text, _ in paragraphs),
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
        if not external_id:
            raise
        existing = await session.scalar(select(Source).where(Source.external_id == external_id))
        if existing is None:
            raise
        return existing
    for number, (text, opinion_part) in enumerate(paragraphs, start=1):
        session.add(SourceParagraph(source_id=source.id, para_no=number, opinion_part=opinion_part, text=text))
    return source


async def _quote_check(
    session: Any, citation: Citation, source: Source, claims: list[Claim]
) -> None:
    paragraphs = list(
        await session.scalars(select(SourceParagraph).where(SourceParagraph.source_id == source.id).order_by(SourceParagraph.para_no))
    )
    for claim in claims:
        if not claim.quote_text:
            continue
        result = check_quote(
            claim.quote_text,
            [
                VerdictParagraph(str(item.id), item.text, item.page, item.opinion_part)
                for item in paragraphs
            ],
            pinpoint_page=_pinpoint_page(citation.pinpoint),
        )
        # ADR-002 permits embeddings (not an LLM judge) for the one semantic
        # question left after deterministic alignment. Bound P1 consumption to
        # 32 source paragraphs per quote.
        if result.semantic_check_status.value == "NOT_CONFIGURED" and get_settings().openai_api_key:
            candidates = _semantic_candidates(claim.quote_text, paragraphs)
            try:
                scores = await cosine_scores(get_settings().openai_api_key, claim.quote_text, candidates)
                result = check_quote(
                    claim.quote_text,
                    [VerdictParagraph(str(item.id), item.text, item.page, item.opinion_part) for item in paragraphs],
                    pinpoint_page=_pinpoint_page(citation.pinpoint),
                    semantic_scores=scores,
                )
            except EmbeddingUnavailable:
                pass
        evidence: dict[str, Any] = {"source_id": str(source.id), "diff": [
            {"op": item.operation, "quote_tokens": list(item.quote_tokens), "source_tokens": list(item.source_tokens)}
            for item in result.diff
        ]}
        if result.evidence:
            evidence["paragraphs"] = [{"para_id": result.evidence.paragraph_id, "page": result.evidence.page}]
        if result.closest_passage:
            evidence["closest_actual_language"] = {
                "paragraph_id": result.closest_passage.paragraph_id,
                "text": result.closest_passage.text,
            }
        await _write_finding(
            session,
            citation,
            "quote",
            result.verdict.value,
            result.confidence,
            [*result.notes, f"SEMANTIC_CHECK_{result.semantic_check_status.value}"],
            evidence,
        )


def _semantic_candidates(quote: str, paragraphs: list[SourceParagraph]) -> list[tuple[str, str]]:
    """Bound request cost while preferring paragraphs with lexical signal."""
    terms = set(re.findall(r"[a-z0-9]+", quote.casefold()))
    ranked = sorted(
        paragraphs,
        key=lambda item: len(terms & set(re.findall(r"[a-z0-9]+", item.text.casefold()))),
        reverse=True,
    )
    return [(str(item.id), item.text) for item in ranked[:32]]


def _pinpoint_page(pinpoint: str | None) -> int | None:
    """Use the first numeric pinpoint for P1's informational page note."""
    if not pinpoint:
        return None
    match = re.search(r"\d+", pinpoint)
    return int(match.group()) if match else None


async def _write_quote_unavailable(session: Any, citation: Citation) -> None:
    claims = list(await session.scalars(select(Claim).where(Claim.citation_id == citation.id)))
    if any(claim.quote_text for claim in claims):
        await _write_finding(session, citation, "quote", "SOURCE_UNAVAILABLE", 0.0, [], {})


async def _write_finding(
    session: Any,
    citation: Citation,
    check: str,
    verdict: str,
    confidence: float | None,
    notes: list[str],
    evidence: dict[str, Any],
) -> Finding:
    existing = await session.scalar(
        select(Finding).where(Finding.citation_id == citation.id, Finding.check == check)
    )
    if existing is None:
        existing = Finding(citation_id=citation.id, check=check, verdict=verdict, confidence=confidence, notes=notes, evidence=evidence)
        session.add(existing)
    else:
        existing.verdict, existing.confidence, existing.notes, existing.evidence = verdict, confidence, notes, evidence
    await session.flush()
    return existing


async def _publish_citation_findings(session: Any, citation: Citation) -> None:
    findings = list(await session.scalars(select(Finding).where(Finding.citation_id == citation.id)))
    for finding in findings:
        await publish_event(
            str(citation.job_id),
            "finding.created",
            {
                "finding_id": str(finding.id),
                "citation_id": str(citation.id),
                "check": finding.check,
                "verdict": finding.verdict,
                "confidence": finding.confidence,
                "created_at": finding.created_at.isoformat(),
            },
        )


async def _maybe_finish_job(session: Any, job_id: uuid.UUID) -> None:
    total = await session.scalar(select(func.count(Citation.id)).where(Citation.job_id == job_id))
    complete = await session.scalar(
        select(func.count(Finding.id))
        .join(Citation, Finding.citation_id == Citation.id)
        .where(Citation.job_id == job_id, Finding.check == "existence")
    )
    if total and total == complete:
        job = await session.get(Job, job_id)
        if job and job.status != JobStatus.COMPLETED.value:
            await _complete_job(session, job)
            await session.commit()
            await publish_event(str(job_id), "job.completed", {"summary": {}})


async def _complete_job(session: Any, job: Job) -> None:
    job.status = JobStatus.COMPLETED.value
    job.completed_at = datetime.now(timezone.utc)
