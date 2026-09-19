"""Deterministic, evidence-first quote fidelity checks (SPEC.md §6.2).

This package deliberately contains no network, embedding, or LLM client.  The
only P1 candidate lookup is a local comparison against the paragraphs supplied
by the CourtListener-resolved opinion (ADR-002 and ADR-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
import difflib
import re
import unicodedata
from typing import Iterable, Literal, Mapping

import yaml


class QuoteVerdict(StrEnum):
    """The quote-fidelity verdict vocabulary defined in SPEC.md §6.2."""

    VERBATIM = "VERBATIM"
    VERBATIM_WITH_PERMITTED_ALTERATIONS = "VERBATIM_WITH_PERMITTED_ALTERATIONS"
    ALTERED = "ALTERED"
    PARAPHRASE_IN_QUOTES = "PARAPHRASE_IN_QUOTES"
    NOT_FOUND_IN_SOURCE = "NOT_FOUND_IN_SOURCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class SemanticCheckStatus(StrEnum):
    """Whether the semantic prerequisite for PARAPHRASE was checked.

    ``NOT_CONFIGURED`` is intentionally exposed so callers never mistake a
    lexical-only NOT_FOUND result for a completed semantic determination.
    """

    NOT_NEEDED = "NOT_NEEDED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class QuoteThresholds:
    verbatim_similarity: float
    altered_similarity_min: float
    paraphrase_semantic_similarity: float


@dataclass(frozen=True, slots=True)
class SourceParagraph:
    """A paragraph fetched for the resolved opinion.

    ``page`` is optional because CourtListener does not always expose a usable
    page mapping.  It is used only for the informational FR-QTE-005 note.
    """

    id: str
    text: str
    page: int | None = None
    opinion_part: str | None = None


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    paragraph_id: str
    text: str
    start: int
    end: int
    page: int | None = None
    opinion_part: str | None = None


@dataclass(frozen=True, slots=True)
class WordDiff:
    """One word-level edit operation, suitable for escaped UI rendering."""

    operation: Literal["equal", "insert", "delete", "replace"]
    quote_tokens: tuple[str, ...]
    source_tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuoteCheckResult:
    verdict: QuoteVerdict
    similarity: float
    confidence: float
    evidence: EvidencePassage | None
    closest_passage: EvidencePassage | None
    diff: tuple[WordDiff, ...]
    notes: tuple[str, ...]
    semantic_check_status: SemanticCheckStatus


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _PatternToken:
    display: str
    matcher: re.Pattern[str]
    altered: bool = False


@dataclass(frozen=True, slots=True)
class _Candidate:
    start: int
    end: int
    similarity: float


_QUOTE_TRANSLATIONS = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201b": "'", "\u2032": "'",
        "\u201c": '"', "\u201d": '"', "\u201f": '"', "\u2033": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": "-", "\u2015": "-", "\u2212": "-",
    }
)
_FOOTNOTE_MARKER = re.compile(r"(?<!\w)(?:\[\d{1,3}\]|\*+|[\u2020\u2021])(?=\s|$)")
_EDITORIAL_BRACKET_NOTE = re.compile(
    r"\[\s*(?:emphasis\s+(?:added|supplied)|citation\s+omitted|internal\s+quotation\s+marks?\s+omitted|alteration\s+in\s+original)\s*\]",
    re.IGNORECASE,
)
_ELLIPSIS = re.compile(r"(?:\.{3,}|\u2026)+")
_TOKEN = re.compile(r"[\w]+(?:['’][\w]+)*", re.UNICODE)


def normalise(text: str, *, casefold: bool = False) -> str:
    """Apply the §6.2 text normalisation rules without mutating display text."""

    value = unicodedata.normalize("NFKC", text).translate(_QUOTE_TRANSLATIONS)
    # PDF extraction commonly produces ``inter-\nrupted`` for one word.
    value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
    value = _FOOTNOTE_MARKER.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold() if casefold else value


def load_thresholds(path: str | Path | None = None) -> QuoteThresholds:
    """Read the committed quote thresholds; threshold changes require an ADR."""

    config_path = Path(path) if path is not None else _find_threshold_config()
    with config_path.open(encoding="utf-8") as handle:
        quote = yaml.safe_load(handle)["quote_fidelity"]
    return QuoteThresholds(
        verbatim_similarity=float(quote["verbatim_similarity"]),
        altered_similarity_min=float(quote["altered_similarity_min"]),
        paraphrase_semantic_similarity=float(quote["paraphrase_semantic_similarity"]),
    )


@lru_cache(maxsize=1)
def _find_threshold_config() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / "config" / "thresholds.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Could not locate committed config/thresholds.yaml")


def check_quote(
    quote: str,
    paragraphs: Iterable[SourceParagraph],
    *,
    pinpoint_page: int | None = None,
    thresholds: QuoteThresholds | None = None,
    semantic_scores: Mapping[str, float] | None = None,
) -> QuoteCheckResult:
    """Compare a brief quotation with supplied opinion paragraphs.

    The returned closest passage is always local to the supplied resolved
    opinion, satisfying P1's ADR-010 fallback.  A result below the lexical
    threshold includes ``NOT_CONFIGURED`` because P1 cannot make the semantic
    determination required for ``PARAPHRASE_IN_QUOTES`` without supplied
    embedding scores.
    """

    threshold_values = thresholds or load_thresholds()
    source_paragraphs = tuple(paragraphs)
    if not source_paragraphs or not any(paragraph.text.strip() for paragraph in source_paragraphs):
        return QuoteCheckResult(
            verdict=QuoteVerdict.SOURCE_UNAVAILABLE,
            similarity=0.0,
            confidence=0.0,
            evidence=None,
            closest_passage=None,
            diff=(),
            notes=(),
            semantic_check_status=SemanticCheckStatus.NOT_NEEDED,
        )

    segments, used_conventions = _quote_segments(quote)
    if not segments:
        # An empty/non-word quote has no mechanically checkable content.
        closest = _whole_paragraph_evidence(source_paragraphs[0])
        return QuoteCheckResult(
            verdict=QuoteVerdict.NOT_FOUND_IN_SOURCE,
            similarity=0.0,
            confidence=0.0,
            evidence=None,
            closest_passage=closest,
            diff=(),
            notes=("EMPTY_QUOTE",),
            semantic_check_status=SemanticCheckStatus.NOT_CONFIGURED,
        )

    best_alignment: tuple[SourceParagraph, tuple[_Candidate, ...], list[_Token]] | None = None
    best_alignment_score = -1.0
    closest: tuple[SourceParagraph, _Candidate, list[_Token]] | None = None
    closest_score = -1.0

    for paragraph in source_paragraphs:
        tokens = _tokens(paragraph.text)
        if not tokens:
            continue
        ordered = _ordered_alignment(segments, tokens)
        if ordered is not None:
            score = min(candidate.similarity for candidate in ordered)
            if score > best_alignment_score:
                best_alignment = (paragraph, ordered, tokens)
                best_alignment_score = score
        candidate = _best_local_candidate(_flatten(segments), tokens)
        if candidate is not None and candidate.similarity > closest_score:
            closest = (paragraph, candidate, tokens)
            closest_score = candidate.similarity

    if best_alignment is not None and best_alignment_score >= threshold_values.altered_similarity_min:
        paragraph, candidates, tokens = best_alignment
        start = candidates[0].start
        end = candidates[-1].end
        evidence = _evidence(paragraph, tokens, start, end)
        diff = _diff_for_segments(segments, candidates, tokens)
        notes: list[str] = []
        if pinpoint_page is not None and paragraph.page is not None and pinpoint_page != paragraph.page:
            notes.append("PINPOINT_MISMATCH")
        if best_alignment_score >= threshold_values.verbatim_similarity:
            verdict = (
                QuoteVerdict.VERBATIM_WITH_PERMITTED_ALTERATIONS
                if used_conventions
                else QuoteVerdict.VERBATIM
            )
            semantic_status = SemanticCheckStatus.NOT_NEEDED
        else:
            verdict = QuoteVerdict.ALTERED
            semantic_status = SemanticCheckStatus.NOT_NEEDED
        return QuoteCheckResult(
            verdict=verdict,
            similarity=round(best_alignment_score, 4),
            confidence=round(best_alignment_score, 4),
            evidence=evidence,
            closest_passage=evidence,
            diff=diff,
            notes=tuple(notes),
            semantic_check_status=semantic_status,
        )

    closest_evidence = None
    if closest is not None:
        paragraph, candidate, tokens = closest
        closest_evidence = _evidence(paragraph, tokens, candidate.start, candidate.end)

    if semantic_scores:
        semantic_match = max(
            (paragraph for paragraph in source_paragraphs if paragraph.id in semantic_scores),
            key=lambda paragraph: semantic_scores[paragraph.id],
            default=None,
        )
        if (
            semantic_match is not None
            and semantic_scores[semantic_match.id] >= threshold_values.paraphrase_semantic_similarity
        ):
            semantic_evidence = _whole_paragraph_evidence(semantic_match)
            return QuoteCheckResult(
                verdict=QuoteVerdict.PARAPHRASE_IN_QUOTES,
                similarity=round(semantic_scores[semantic_match.id], 4),
                confidence=round(semantic_scores[semantic_match.id], 4),
                evidence=semantic_evidence,
                closest_passage=semantic_evidence,
                diff=_word_diff(_display_tokens(segments), _values(_tokens(semantic_match.text))),
                notes=("SEMANTIC_MATCH",),
                semantic_check_status=SemanticCheckStatus.COMPLETED,
            )

    semantic_completed = semantic_scores is not None
    return QuoteCheckResult(
        verdict=QuoteVerdict.NOT_FOUND_IN_SOURCE,
        similarity=round(max(closest_score, 0.0), 4),
        confidence=round(max(closest_score, 0.0), 4),
        evidence=None,
        closest_passage=closest_evidence,
        diff=_word_diff(_display_tokens(segments), _values(_tokens(closest_evidence.text)) if closest_evidence else []),
        notes=("SEMANTIC_NO_MATCH",) if semantic_completed else ("SEMANTIC_PROVIDER_REQUIRED",),
        semantic_check_status=(SemanticCheckStatus.COMPLETED if semantic_completed else SemanticCheckStatus.NOT_CONFIGURED),
    )


def _quote_segments(quote: str) -> tuple[list[list[_PatternToken]], bool]:
    value = normalise(quote, casefold=True)
    used_ellipsis = bool(_ELLIPSIS.search(value))
    used_editorial_note = bool(_EDITORIAL_BRACKET_NOTE.search(value))
    value = _EDITORIAL_BRACKET_NOTE.sub(" ", value)
    raw_segments = [part.strip() for part in _ELLIPSIS.split(value)]
    segments: list[list[_PatternToken]] = []
    used_bracket = False
    for raw in raw_segments:
        patterns: list[_PatternToken] = []
        for match in _TOKEN.finditer(raw):
            token = match.group()
            # The token regex does not include square brackets, so recover
            # bracket syntax from its immediate surrounding source token below.
            patterns.append(_literal_pattern(token))
        # Preserve `[t]he` / `pre[fix]` forms, which contain punctuation omitted
        # by _TOKEN.  Splitting on whitespace makes each legal word one pattern.
        patterns = [_pattern_for_legal_token(item) for item in raw.split() if _has_word(item)]
        if any(pattern.altered for pattern in patterns):
            used_bracket = True
        if patterns:
            segments.append(patterns)
    return segments, used_ellipsis or used_bracket or used_editorial_note


def _has_word(value: str) -> bool:
    return bool(_TOKEN.search(value))


def _literal_pattern(value: str) -> _PatternToken:
    return _PatternToken(value, re.compile(rf"^{re.escape(value)}$"))


def _pattern_for_legal_token(raw: str) -> _PatternToken:
    # Retain words/apostrophes and bracket expressions, discarding quotation and
    # punctuation characters around them.
    cleaned = re.sub(r"[^\w\[\]'’]", "", raw).replace("’", "'")
    if "[" not in cleaned or "]" not in cleaned:
        match = _TOKEN.search(cleaned)
        return _literal_pattern(match.group() if match else cleaned)
    pieces: list[str] = []
    position = 0
    altered = False
    for bracket in re.finditer(r"\[[^\]]*\]", cleaned):
        literal = cleaned[position:bracket.start()]
        if literal:
            pieces.append(re.escape(literal))
        # A bracketed word is a permitted substituted portion of this one token.
        pieces.append(r"[\w']*")
        altered = True
        position = bracket.end()
    suffix = cleaned[position:]
    if suffix:
        pieces.append(re.escape(suffix))
    pattern = "".join(pieces) or r"[\w']+"
    return _PatternToken(cleaned, re.compile(rf"^{pattern}$"), altered=altered)


def _tokens(text: str) -> list[_Token]:
    # Token values use all normalisation rules while locations stay in the
    # original paragraph, keeping UI evidence spans meaningful.
    display = unicodedata.normalize("NFKC", text).translate(_QUOTE_TRANSLATIONS)
    display = _FOOTNOTE_MARKER.sub(lambda marker: " " * len(marker.group()), display)
    normalised_chars: list[str] = []
    source_offsets: list[int] = []
    index = 0
    while index < len(display):
        # De-hyphenate only a PDF line-break hyphen, retaining the original
        # character positions of the joined word for evidence rendering.
        if display[index] == "-" and index and display[index - 1].isalnum():
            following = re.match(r"\s*\n\s*(?=\w)", display[index + 1:])
            if following:
                index += 1 + following.end()
                continue
        normalised_chars.append(display[index])
        source_offsets.append(index)
        index += 1
    normalised = "".join(normalised_chars)
    return [
        _Token(
            match.group().casefold(),
            source_offsets[match.start()],
            source_offsets[match.end() - 1] + 1,
        )
        for match in _TOKEN.finditer(normalised)
    ]


def _ordered_alignment(
    segments: list[list[_PatternToken]], source: list[_Token]
) -> tuple[_Candidate, ...] | None:
    candidates_by_segment = [_segment_candidates(segment, source) for segment in segments]
    if any(not candidates for candidates in candidates_by_segment):
        return None
    states: list[tuple[tuple[_Candidate, ...], float]] = [((candidate,), candidate.similarity) for candidate in candidates_by_segment[0]]
    for candidates in candidates_by_segment[1:]:
        next_states: list[tuple[tuple[_Candidate, ...], float]] = []
        for prior, score in states:
            for candidate in candidates:
                if prior[-1].end <= candidate.start:
                    next_states.append((prior + (candidate,), min(score, candidate.similarity)))
        if not next_states:
            return None
        # Enough candidates for repeated language while bounding long opinions.
        next_states.sort(key=lambda state: (state[1], -state[0][-1].start), reverse=True)
        states = next_states[:48]
    return max(states, key=lambda state: (state[1], -state[0][0].start))[0]


def _segment_candidates(segment: list[_PatternToken], source: list[_Token]) -> list[_Candidate]:
    length = len(segment)
    slack = max(2, round(length * 0.30))
    candidates: list[_Candidate] = []
    # Do not exhaustively pair every source offset with every permitted window
    # length.  On a long opinion and a long quotation that becomes quadratic
    # enough to starve the review queue. Four-token anchors are deterministic
    # local retrieval: a high-similarity alignment must contain one unless it
    # is already an extremely weak candidate. The bounded fallback below still
    # produces an honest NOT_FOUND_IN_SOURCE/semantic-pending result.
    starts = _anchored_starts(segment, source)
    window_lengths = sorted(range(max(1, length - slack), length + slack + 1), key=lambda value: abs(value - length))
    checks = 0
    for start in starts:
        if start < 0 or start >= len(source):
            continue
        added = False
        for window_length in window_lengths:
            if start + window_length > len(source):
                continue
            end = start + window_length
            candidates.append(_Candidate(start, end, _token_similarity(segment, source[start:end])))
            added = True
            checks += 1
            if checks >= 64:
                break
        if not added:
            # Preserve a closest local passage even when an anchor lies near
            # the end of the paragraph (for example an out-of-order ellipsis).
            candidates.append(_Candidate(start, len(source), _token_similarity(segment, source[start:])))
            checks += 1
        if checks >= 64:
            break
    candidates.sort(key=lambda candidate: (candidate.similarity, -(candidate.end - candidate.start), -candidate.start), reverse=True)
    return candidates[:32]


def _anchored_starts(segment: list[_PatternToken], source: list[_Token]) -> list[int]:
    if not source:
        return []
    width = min(4, len(segment))
    starts: list[int] = []
    seen: set[int] = set()
    # Use several quote positions so a common opening ("the court") does not
    # crowd out a later, more discriminating phrase.
    offsets = sorted({0, max(0, (len(segment) - width) // 2), max(0, len(segment) - width)})
    for offset in offsets:
        anchor = segment[offset:offset + width]
        for position in range(0, len(source) - width + 1):
            if all(pattern.matcher.fullmatch(source[position + index].value) for index, pattern in enumerate(anchor)):
                start = position - offset
                if start not in seen:
                    starts.append(start)
                    seen.add(start)
                    if len(starts) >= 24:
                        return starts
    if starts:
        return starts
    # A bounded spread across the opinion preserves a deterministic closest
    # passage when no exact anchor exists, without unbounded CPU work.
    step = max(1, len(source) // 24)
    return list(range(0, len(source), step))[:24]


def _best_local_candidate(pattern: list[_PatternToken], source: list[_Token]) -> _Candidate | None:
    if not source or not pattern:
        return None
    candidates = _segment_candidates(pattern, source)
    return candidates[0] if candidates else None


def _flatten(segments: list[list[_PatternToken]]) -> list[_PatternToken]:
    return [token for segment in segments for token in segment]


def _token_similarity(pattern: list[_PatternToken], source: list[_Token]) -> float:
    # Levenshtein at word level gives inserted/deleted/changed words a visible,
    # stable cost and lets bracket patterns match without a penalty.
    previous = list(range(len(source) + 1))
    for row, wanted in enumerate(pattern, start=1):
        current = [row]
        for column, actual in enumerate(source, start=1):
            substitute = previous[column - 1] + (0 if wanted.matcher.fullmatch(actual.value) else 1)
            current.append(min(previous[column] + 1, current[column - 1] + 1, substitute))
        previous = current
    distance = previous[-1]
    return 1.0 - distance / max(len(pattern), len(source), 1)


def _evidence(paragraph: SourceParagraph, tokens: list[_Token], start: int, end: int) -> EvidencePassage:
    return EvidencePassage(
        paragraph_id=paragraph.id,
        text=paragraph.text[tokens[start].start:tokens[end - 1].end],
        start=tokens[start].start,
        end=tokens[end - 1].end,
        page=paragraph.page,
        opinion_part=paragraph.opinion_part,
    )


def _whole_paragraph_evidence(paragraph: SourceParagraph) -> EvidencePassage:
    return EvidencePassage(paragraph.id, paragraph.text, 0, len(paragraph.text), paragraph.page, paragraph.opinion_part)


def _values(tokens: list[_Token]) -> list[str]:
    return [token.value for token in tokens]


def _display_tokens(segments: list[list[_PatternToken]]) -> list[str]:
    return [token.display for segment in segments for token in segment]


def _diff_for_segments(
    segments: list[list[_PatternToken]], candidates: tuple[_Candidate, ...], source: list[_Token]
) -> tuple[WordDiff, ...]:
    # Diff segments independently: text omitted by a legal ellipsis is not
    # represented as a discrepancy.
    output: list[WordDiff] = []
    for segment, candidate in zip(segments, candidates, strict=True):
        actual = source[candidate.start:candidate.end]
        # A permitted bracket change is evidence of the same word, not a word
        # difference.  Substitute the matched source spelling in the comparison
        # view so consumers do not render a misleading replacement hunk.
        quote_for_diff = [
            source_token.value if pattern.altered and pattern.matcher.fullmatch(source_token.value) else pattern.display
            for pattern, source_token in zip(segment, actual, strict=False)
        ]
        output.extend(_word_diff(quote_for_diff, _values(actual)))
    return tuple(output)


def _word_diff(quote_tokens: list[str], source_tokens: list[str]) -> tuple[WordDiff, ...]:
    operations: list[WordDiff] = []
    matcher = difflib.SequenceMatcher(a=quote_tokens, b=source_tokens, autojunk=False)
    for operation, quote_start, quote_end, source_start, source_end in matcher.get_opcodes():
        operations.append(
            WordDiff(
                operation=operation,  # type: ignore[arg-type]
                quote_tokens=tuple(quote_tokens[quote_start:quote_end]),
                source_tokens=tuple(source_tokens[source_start:source_end]),
            )
        )
    return tuple(operations)


__all__ = [
    "EvidencePassage",
    "QuoteCheckResult",
    "QuoteThresholds",
    "QuoteVerdict",
    "SemanticCheckStatus",
    "SourceParagraph",
    "WordDiff",
    "check_quote",
    "load_thresholds",
    "normalise",
]
