"""Typed, persistence-neutral records produced by the P1 pipeline.

Offsets are always offsets into one PDF page, never a lossy concatenated text
buffer.  The worker can persist these fields directly into the P1 schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class TextSpan:
    """A half-open range on a single page of the uploaded PDF."""

    page: int  # one based, matching the page number a user sees
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.page < 1 or self.start < 0 or self.end < self.start:
            raise ValueError("invalid page text span")


@dataclass(frozen=True, slots=True)
class NormalisedPage:
    """Processing text and a reversible character-level map to `PageText.raw_text`.

    `normalised_to_original[i]` is the raw-page character that generated
    `text[i]`.  Ranges map conservatively: removed separators between two
    retained characters are included in the original span.
    """

    text: str
    normalised_to_original: tuple[int, ...]

    def original_span(self, page: int, start: int, end: int) -> TextSpan:
        if not 0 <= start <= end <= len(self.text):
            raise ValueError("normalised span is outside the page")
        if start == end:
            anchor = self.normalised_to_original[start] if start < len(self.text) else 0
            return TextSpan(page, anchor, anchor)
        origins = self.normalised_to_original[start:end]
        return TextSpan(page, min(origins), max(origins) + 1)


@dataclass(frozen=True, slots=True)
class PageText:
    page: int
    raw_text: str
    normalised: NormalisedPage


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    pages: tuple[PageText, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)


class CitationKind(StrEnum):
    FULL = "full"
    SHORT = "short"
    ID = "id"
    SUPRA = "supra"


class AntecedentState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class QuoteClaim:
    """Attached quotation in its display and processing representations."""

    text: str
    processing_text: str
    original_span: TextSpan
    processing_span: TextSpan
    is_block: bool = False


@dataclass(frozen=True, slots=True)
class Citation:
    """A parsed case citation plus the evidence needed by downstream checks."""

    id: str
    raw_text: str
    normalized: str
    kind: CitationKind
    original_span: TextSpan
    processing_span: TextSpan
    # A deterministic, bounded sentence window used by P2 proposition
    # extraction.  It is deliberately a real original-text span so model
    # output can be located again without trusting model-reported offsets.
    context_span: TextSpan
    pinpoint: tuple[str, ...] = ()
    case_name: str | None = None
    court_hint: str | None = None
    year_hint: int | None = None
    antecedent_id: str | None = None
    antecedent_state: AntecedentState = AntecedentState.NOT_APPLICABLE
    quote: QuoteClaim | None = None
    metadata: dict[str, str | int | None] = field(default_factory=dict)
