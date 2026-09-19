"""Markdown report formats backed only by persisted review evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


# OVERVIEW.md §6; keep in sync with apps/web/src/app/page.tsx.
DISCLAIMER = (
    "PinCite reports differences between a document and the sources it cites. "
    "It is not legal advice and does not assess anyone's intent. Always review "
    "the linked source text yourself."
)

_POSITIVE_VERDICTS = {
    "VERIFIED",
    "VERIFIED_OFFICIAL",
    "VERBATIM",
    "VERBATIM_WITH_PERMITTED_ALTERATIONS",
    "SUPPORTS",
}


def render_markdown(
    job: Any,
    citations: Sequence[Any],
    sources_by_id: Mapping[str, Any],
    report_format: str,
) -> str:
    """Render an FR-RPT fix-list or evidence-table report in document order."""

    ordered = sorted(citations, key=lambda item: (item.page is None, item.page or 0, item.start_offset or 0, str(item.id)))
    heading = "Fix list" if report_format == "fixlist" else "Evidence table"
    lines = [f"# PinCite {heading}", "", f"Document: {_safe_text(job.filename)}", "", f"> {DISCLAIMER}", ""]
    if report_format == "fixlist":
        lines.extend(_render_fix_list(ordered, sources_by_id))
    elif report_format == "evidence":
        lines.extend(_render_evidence_table(ordered, sources_by_id))
    else:
        raise ValueError("report_format must be 'fixlist' or 'evidence'")
    lines.extend(["", "## Provenance note", "", "Source links and retrieval metadata below identify the material PinCite reviewed."])
    return "\n".join(lines).strip() + "\n"


def _render_fix_list(citations: Sequence[Any], sources_by_id: Mapping[str, Any]) -> list[str]:
    lines: list[str] = ["## Items to review", ""]
    count = 0
    for citation in citations:
        findings = [item for item in getattr(citation, "findings", []) if _is_issue(item)]
        if not findings:
            continue
        count += 1
        source = _source_for(citation, sources_by_id)
        lines.extend([f"### {_safe_text(citation.raw_text)}", "", f"Brief page: {citation.page or 'not available'}"])
        for finding in sorted(findings, key=lambda item: (item.created_at, str(item.id))):
            lines.append(f"- **{_safe_text(finding.check)} — {_neutral_verdict(finding.verdict)}.**")
            brief_text = _brief_text(citation, finding)
            source_text = _source_text(finding, source)
            if brief_text:
                lines.append(f"  - Brief text: “{_safe_text(brief_text)}”")
            if source_text:
                lines.append(f"  - Source language reviewed: “{_safe_text(source_text)}”")
            link = _source_link(citation, source)
            if link:
                lines.append(f"  - Source: {link}")
            provenance = _provenance(source)
            if provenance:
                lines.append(f"  - Provenance: {provenance}")
        lines.append("")
    if not count:
        lines.extend(["No items requiring review were found in the completed checks.", ""])
    return lines


def _render_evidence_table(citations: Sequence[Any], sources_by_id: Mapping[str, Any]) -> list[str]:
    lines = [
        "## Evidence table",
        "",
        "| Citation | Brief page | Check | Result | Brief text | Source text | Source link | Provenance |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for citation in citations:
        source = _source_for(citation, sources_by_id)
        for finding in sorted(getattr(citation, "findings", []), key=lambda item: (item.created_at, str(item.id))):
            row = (
                _safe_cell(citation.raw_text),
                str(citation.page or "—"),
                _safe_cell(finding.check),
                _safe_cell(_neutral_verdict(finding.verdict)),
                _safe_cell(_brief_text(citation, finding) or "—"),
                _safe_cell(_source_text(finding, source) or "—"),
                _safe_cell(_source_link(citation, source) or "—"),
                _safe_cell(_provenance(source) or "—"),
            )
            lines.append("| " + " | ".join(row) + " |")
    return lines


def _is_issue(finding: Any) -> bool:
    return str(getattr(finding, "verdict", "")) not in _POSITIVE_VERDICTS


def _neutral_verdict(verdict: str) -> str:
    labels = {
        "NOT_FOUND_ANYWHERE": "Could not be located after the available checks",
        "NOT_IN_DATABASE": "Could not be located in CourtListener",
        "NOT_FOUND_IN_SOURCE": "The quoted language does not appear in the source material reviewed",
        "ALTERED": "The quoted language differs from the source material reviewed",
        "PARAPHRASE_IN_QUOTES": "The quoted language appears similar to, but differs from, source language",
        "CONTRADICTS": "The source passages point in a different direction",
        "NOT_ADDRESSED": "The source passages reviewed do not address this point",
        "UNVERIFIABLE": "The available material did not allow verification",
        "SOURCE_UNAVAILABLE": "The source material was unavailable for this comparison",
        "AMBIGUOUS": "More than one possible source remained",
        "WEAKLY_CORROBORATED": "A matching mention was found, but the source was not verified",
    }
    return labels.get(str(verdict), str(verdict).replace("_", " ").capitalize())


def _source_for(citation: Any, sources_by_id: Mapping[str, Any]) -> Any | None:
    acquisition = getattr(citation, "source_acquisition", None)
    source_id = getattr(acquisition, "source_id", None)
    return sources_by_id.get(str(source_id)) if source_id else None


def _brief_text(citation: Any, finding: Any) -> str:
    claims = list(getattr(citation, "claims", []))
    claim = claims[0] if claims else None
    if getattr(finding, "check", None) == "quote" and claim and getattr(claim, "quote_text", None):
        return str(claim.quote_text)
    if getattr(finding, "check", None) == "proposition" and claim and getattr(claim, "proposition_text", None):
        return str(claim.proposition_text)
    return str(getattr(citation, "raw_text", ""))


def _source_text(finding: Any, source: Any | None) -> str | None:
    evidence = getattr(finding, "evidence", {}) or {}
    closest = evidence.get("closest_actual_language") if isinstance(evidence, dict) else None
    if isinstance(closest, dict) and isinstance(closest.get("text"), str):
        return closest["text"]
    ids: list[str] = []
    if isinstance(evidence, dict):
        for key in ("paragraph_id",):
            if isinstance(evidence.get(key), str):
                ids.append(evidence[key])
        for key in ("cited_paragraph_ids",):
            if isinstance(evidence.get(key), list):
                ids.extend(item for item in evidence[key] if isinstance(item, str))
        if isinstance(evidence.get("paragraphs"), list):
            ids.extend(
                item["para_id"] for item in evidence["paragraphs"]
                if isinstance(item, dict) and isinstance(item.get("para_id"), str)
            )
    if source is None:
        return None
    by_uuid = {str(item.id): item.text for item in getattr(source, "paragraphs", [])}
    by_number = {f"{source.id}:p{item.para_no}": item.text for item in getattr(source, "paragraphs", [])}
    passages = [by_uuid.get(item) or by_number.get(item) for item in ids]
    found = [str(item) for item in passages if item]
    return " / ".join(found) if found else None


def _source_link(citation: Any, source: Any | None) -> str | None:
    provenance = getattr(source, "provenance", None) if source is not None else None
    if provenance and getattr(provenance, "url", None):
        return str(provenance.url)
    acquisition = getattr(citation, "source_acquisition", None)
    return str(acquisition.source_url) if acquisition and getattr(acquisition, "source_url", None) else None


def _provenance(source: Any | None) -> str | None:
    provenance = getattr(source, "provenance", None) if source is not None else None
    if provenance is None:
        return None
    retrieved_at = getattr(provenance, "retrieved_at", None)
    timestamp = retrieved_at.isoformat() if isinstance(retrieved_at, datetime) else "unknown time"
    return f"method={provenance.method}; retrieved={timestamp}; sha256={provenance.sha256}"


_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[redacted secret]"),
    (re.compile(r"\b(?:postgres(?:ql)?|redis)://[^\s|)]+", re.IGNORECASE), "[redacted connection string]"),
    (re.compile(r"\b[A-Za-z]:\\[^\s|)]+"), "[redacted local path]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[redacted email]"),
)


def _safe_text(value: object) -> str:
    text = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_cell(value: object) -> str:
    return _safe_text(value).replace("|", "\\|")
