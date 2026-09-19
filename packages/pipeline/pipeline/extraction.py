"""Deterministic case-citation, antecedent, pinpoint, and quote extraction."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Sequence

from eyecite import get_citations
from eyecite.models import FullCaseCitation, IdCitation, ShortCaseCitation, SupraCitation

from .models import (
    AntecedentState,
    Citation,
    CitationKind,
    IngestedDocument,
    PageText,
    QuoteClaim,
    TextSpan,
)

_CASE_TYPES = (FullCaseCitation, ShortCaseCitation, IdCitation, SupraCitation)
_PINPOINT = re.compile(r"(?:at\s+)?(\d+(?:[-–]\d+)?(?:\s*,\s*\d+(?:[-–]\d+)?)*)", re.I)
_QUOTED = re.compile(r'(?P<open>["“])(?P<text>.*?)(?P<close>["”])', re.S)


def _citation_kind(value: object) -> CitationKind:
    if isinstance(value, FullCaseCitation):
        return CitationKind.FULL
    if isinstance(value, ShortCaseCitation):
        return CitationKind.SHORT
    if isinstance(value, IdCitation):
        return CitationKind.ID
    if isinstance(value, SupraCitation):
        return CitationKind.SUPRA
    raise TypeError(f"not a case citation: {type(value)!r}")


def _full_span(value: object) -> tuple[int, int]:
    """Use eyecite's full context range where available, falling back safely."""

    start, end = value.span()  # type: ignore[union-attr]
    full_start = getattr(value, "full_span_start", None)
    full_end = getattr(value, "full_span_end", None)
    if full_start is not None and full_end is not None and full_start < full_end:
        return full_start, full_end
    return start, end


def _normalise_citation(text: str) -> str:
    return " ".join(text.casefold().split())


def _metadata(value: object) -> dict[str, str | int | None]:
    meta = getattr(value, "metadata", None)
    result: dict[str, str | int | None] = {}
    for name in ("plaintiff", "defendant", "court", "year", "antecedent_guess", "pin_cite"):
        data = getattr(meta, name, None)
        result[name] = data
    return result


def _pinpoints(value: object) -> tuple[str, ...]:
    raw = getattr(getattr(value, "metadata", None), "pin_cite", None)
    if not raw:
        return ()
    match = _PINPOINT.search(str(raw))
    if not match:
        return ()
    return tuple(part.strip() for part in re.split(r"\s*,\s*", match.group(1)))


def _case_name(meta: dict[str, str | int | None]) -> str | None:
    plaintiff, defendant = meta.get("plaintiff"), meta.get("defendant")
    if plaintiff and defendant:
        return f"{plaintiff} v. {defendant}"
    return None


def _sentence_bounds(text: str, position: int) -> tuple[int, int, int, int]:
    """Return current and immediately preceding sentence bounds.

    The rule purposefully favours the broad sentence around a citation.  It
    handles punctuation followed by whitespace and avoids treating ``U.S.`` as
    a boundary because it is not followed by a new sentence-like character.
    """

    starts = [0]
    for match in re.finditer(r"[.!?][\"')\]]*(?=\s+[A-Z\"'])", text):
        # The party separator in ``Roe v. Wade`` is not a sentence boundary.
        # (Other reporter abbreviations normally fail the uppercase-lookahead
        # because they are followed by a page number.)
        if text[match.start()] == "." and re.search(r"\bv\.$", text[: match.start() + 1], re.I):
            continue
        starts.append(match.end())
    current_start = max((start for start in starts if start <= position), default=0)
    after = [start for start in starts if start > position]
    current_end = after[0] if after else len(text)
    previous_starts = [start for start in starts if start < current_start]
    previous_start = previous_starts[-1] if previous_starts else current_start
    previous_end = current_start
    return current_start, current_end, previous_start, previous_end


def _find_quote_in_range(text: str, start: int, end: int) -> tuple[int, int] | None:
    quoted: list[tuple[int, int]] = []
    for match in _QUOTED.finditer(text, start, end):
        # Pair only matching style quote marks: ASCII pairs with ASCII and curly
        # opening with curly closing.  A quote must have real contents.
        if match.group("open") == '"' and match.group("close") != '"':
            continue
        if match.group("open") == "“" and match.group("close") != "”":
            continue
        if match.group("text").strip():
            quoted.append((match.start("text"), match.end("text")))
    return quoted[-1] if quoted else None


def _find_associated_block_quote(text: str, citation_start: int) -> tuple[int, int] | None:
    """Recognise a preceding multi-line quoted block without guessing prose.

    PDF extraction commonly retains line breaks for indented blocks.  We accept
    only a contiguous 2+ line region immediately before the citation where each
    line is visibly quoted or indented.  Ordinary multi-line paragraphs are not
    treated as quotations.
    """

    prefix = text[:citation_start]
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", prefix))
    # Ignore whitespace between the block and the immediately following cite.
    while lines and not lines[-1].group().strip():
        lines.pop()
    block: list[re.Match[str]] = []
    while lines:
        line = lines.pop()
        stripped = line.group().rstrip("\r\n")
        if not stripped.strip() or not re.match(r"^[ \t]+\S", stripped):
            break
        block.append(line)
    if len(block) < 2:
        return None
    block.reverse()
    first_line = block[0].group().lstrip()
    last_line = block[-1].group().rstrip("\r\n ").rstrip()
    if not first_line.startswith(('"', '“')) or not last_line.endswith(('"', '”')):
        return None
    start = block[0].start() + block[0].group().index(first_line) + 1
    end = block[-1].start() + len(last_line) - 1
    return (start, end) if end > start else None


def _quote_for(page: PageText, citation_start: int) -> QuoteClaim | None:
    text = page.normalised.text
    # Prefer a clearly delimited multi-line block. Treating it as ordinary
    # inline quotation would stop at the first line's closing/next opening
    # mark and lose the associated block evidence.
    raw_citation = page.normalised.original_span(page.page, citation_start, citation_start + 1).start
    raw_block = _find_associated_block_quote(page.raw_text, raw_citation)
    if raw_block is not None:
        raw_start, raw_end = raw_block
        processing_indexes = [
            index
            for index, origin in enumerate(page.normalised.normalised_to_original)
            if raw_start <= origin < raw_end
        ]
        if processing_indexes:
            start, end = processing_indexes[0], processing_indexes[-1] + 1
            return QuoteClaim(
                text=page.raw_text[raw_start:raw_end],
                processing_text=text[start:end],
                original_span=TextSpan(page.page, raw_start, raw_end),
                processing_span=TextSpan(page.page, start, end),
                is_block=True,
            )

    current_start, current_end, previous_start, previous_end = _sentence_bounds(text, citation_start)
    found = _find_quote_in_range(text, current_start, current_end)
    if found is None:
        found = _find_quote_in_range(text, previous_start, previous_end)
    is_block = False
    if found is None:
        return None

    start, end = found
    original_span = page.normalised.original_span(page.page, start, end)
    return QuoteClaim(
        text=page.raw_text[original_span.start:original_span.end],
        processing_text=text[start:end],
        original_span=original_span,
        processing_span=TextSpan(page.page, start, end),
        is_block=is_block,
    )


def _matches_antecedent(candidate: Citation, guess: str | None) -> bool:
    if not guess:
        return False
    needle = guess.casefold()
    return needle in (candidate.case_name or "").casefold() or needle in candidate.raw_text.casefold()


def _resolve_antecedents(citations: Sequence[Citation]) -> list[Citation]:
    resolved: list[Citation] = []
    full: list[Citation] = []
    last_resolved: Citation | None = None
    for citation in citations:
        if citation.kind is CitationKind.FULL:
            updated = replace(citation, antecedent_state=AntecedentState.NOT_APPLICABLE)
            full.append(updated)
            last_resolved = updated
        elif citation.kind is CitationKind.ID:
            if last_resolved is None:
                updated = replace(citation, antecedent_state=AntecedentState.UNRESOLVED)
            else:
                updated = replace(
                    citation,
                    antecedent_id=last_resolved.antecedent_id or last_resolved.id,
                    antecedent_state=AntecedentState.RESOLVED,
                )
                last_resolved = updated
        else:
            guess = citation.metadata.get("antecedent_guess")
            guess_text = str(guess) if guess else None
            antecedent = next((item for item in reversed(full) if _matches_antecedent(item, guess_text)), None)
            if antecedent is None:
                updated = replace(citation, antecedent_state=AntecedentState.UNRESOLVED)
            else:
                updated = replace(
                    citation,
                    antecedent_id=antecedent.id,
                    antecedent_state=AntecedentState.RESOLVED,
                )
                last_resolved = updated
        resolved.append(updated)
    return resolved


def extract_citations(document: IngestedDocument) -> tuple[Citation, ...]:
    """Extract every eyecite case citation and preserve unresolved references.

    Parsing happens per page so every public span is a real PDF page span.  The
    resolver operates over the resulting document order, allowing *id.* and
    short forms on later pages to link to a prior full citation.
    """

    citations: list[Citation] = []
    counter = 0
    for page in document.pages:
        for parsed in get_citations(page.normalised.text):
            if not isinstance(parsed, _CASE_TYPES):
                continue
            start, end = _full_span(parsed)
            # Eyecite's contextual range can include leading signal words. Keep
            # it only when sane; citations must remain visible in the page.
            start = max(0, start)
            end = min(len(page.normalised.text), end)
            if end <= start:
                continue
            meta = _metadata(parsed)
            raw_span = page.normalised.original_span(page.page, start, end)
            counter += 1
            citation = Citation(
                id=f"citation-{counter}",
                raw_text=page.raw_text[raw_span.start:raw_span.end],
                normalized=_normalise_citation(page.normalised.text[start:end]),
                kind=_citation_kind(parsed),
                original_span=raw_span,
                processing_span=TextSpan(page.page, start, end),
                pinpoint=_pinpoints(parsed),
                case_name=_case_name(meta),
                court_hint=str(meta["court"]) if meta.get("court") else None,
                year_hint=int(meta["year"]) if str(meta.get("year") or "").isdigit() else None,
                quote=_quote_for(page, start),
                metadata=meta,
            )
            citations.append(citation)
    return tuple(_resolve_antecedents(citations))
