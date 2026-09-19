"""Pure existence-verdict and investigator matching rules (SPEC.md §6.1).

This module deliberately has no browser, database, or network dependency.  It
keeps the safety-critical decision to accept an investigator candidate
reproducible and independently testable, just as ``proposition.py`` validates
the judge output before it becomes a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from enum import StrEnum
from pathlib import Path
import re

import yaml

from . import _find_threshold_config


class ExistenceVerdict(StrEnum):
    """The existence vocabulary defined by SPEC.md §6.1."""

    VERIFIED = "VERIFIED"
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    WEAKLY_CORROBORATED = "WEAKLY_CORROBORATED"
    NOT_FOUND_ANYWHERE = "NOT_FOUND_ANYWHERE"
    AMBIGUOUS = "AMBIGUOUS"
    PENDING = "PENDING"
    # Kept for pipeline-internal failures which have no citation context to
    # investigate (for example an unresolved short form).
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class CitationContext:
    """The brief facts which can corroborate an extracted candidate."""

    case_name: str | None
    court: str | None = None
    year: int | None = None
    docket_no: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    """A structured official-source candidate from Stagehand extraction."""

    case_name: str | None
    court: str | None = None
    decision_date: str | None = None
    docket_no: str | None = None
    opinion_text: str | None = None


@dataclass(frozen=True, slots=True)
class InvestigatorThresholds:
    """Approved matching policy and bounded-run limits for P3."""

    name_similarity_threshold: float
    max_searches: int
    max_fetches: int
    max_wall_time_seconds: float
    official_search_domains: tuple[OfficialSearchDomain, ...]


@dataclass(frozen=True, slots=True)
class OfficialSearchDomain:
    """A deliberately narrow route from citation metadata to an official host."""

    domain: str
    court_hint: str | None = None
    citation_marker: str | None = None


def load_investigator_thresholds(
    path: str | Path | None = None,
) -> InvestigatorThresholds:
    """Load the ADR-013 investigator policy from committed configuration."""

    config_path = Path(path) if path is not None else _find_threshold_config()
    with config_path.open(encoding="utf-8") as handle:
        investigator = yaml.safe_load(handle)["investigator"]
    search_domains: list[OfficialSearchDomain] = []
    for entry in investigator.get("official_search_domains", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("domain"), str):
            raise ValueError("invalid official investigator search-domain entry")
        court_hint = entry.get("court_hint")
        citation_marker = entry.get("citation_marker")
        if court_hint is not None and not isinstance(court_hint, str):
            raise ValueError("official search-domain court_hint must be text")
        if citation_marker is not None and not isinstance(citation_marker, str):
            raise ValueError("official search-domain citation_marker must be text")
        if not court_hint and not citation_marker:
            raise ValueError("official search-domain entry needs a selector")
        search_domains.append(
            OfficialSearchDomain(
                domain=entry["domain"],
                court_hint=court_hint,
                citation_marker=citation_marker,
            )
        )

    return InvestigatorThresholds(
        name_similarity_threshold=float(investigator["name_similarity_threshold"]),
        max_searches=int(investigator["max_searches"]),
        max_fetches=int(investigator["max_fetches"]),
        max_wall_time_seconds=float(investigator["max_wall_time_seconds"]),
        official_search_domains=tuple(search_domains),
    )


def case_name_similarity(left: str, right: str) -> float:
    """Compare case names after conservative display-independent normalisation."""

    left_normalized = _normalise_name(left)
    right_normalized = _normalise_name(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return difflib.SequenceMatcher(None, left_normalized, right_normalized).ratio()


def match_candidate(
    candidate: ExtractedCandidate,
    citation: CitationContext,
    *,
    name_similarity_threshold: float,
) -> bool:
    """Apply FR-INV-005 without accepting a name-only match.

    The caller supplies the policy threshold.  Its final calibrated value is a
    verdict-adjacent ADR decision, rather than an implicit implementation
    default in this module.
    """

    if not 0.0 <= name_similarity_threshold <= 1.0:
        raise ValueError("name_similarity_threshold must be between 0 and 1")
    if not citation.case_name or not candidate.case_name:
        return False
    if (
        case_name_similarity(citation.case_name, candidate.case_name)
        < name_similarity_threshold
    ):
        return False
    return any(
        (
            _same_value(citation.court, candidate.court),
            _same_year(citation.year, candidate.decision_date),
            _same_docket(citation.docket_no, candidate.docket_no),
        )
    )


def _normalise_name(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _same_value(left: str | None, right: str | None) -> bool:
    return bool(left and right and _normalise_name(left) == _normalise_name(right))


def _same_year(year: int | None, decision_date: str | None) -> bool:
    if year is None or not decision_date:
        return False
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", decision_date)
    return bool(match and int(match.group(1)) == year)


def _same_docket(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_normalized = re.sub(r"[^a-z0-9]", "", left.casefold())
    right_normalized = re.sub(r"[^a-z0-9]", "", right.casefold())
    return bool(left_normalized and left_normalized == right_normalized)


__all__ = [
    "CitationContext",
    "ExistenceVerdict",
    "ExtractedCandidate",
    "InvestigatorThresholds",
    "OfficialSearchDomain",
    "case_name_similarity",
    "load_investigator_thresholds",
    "match_candidate",
]
