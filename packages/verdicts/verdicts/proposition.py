"""Pure proposition-support verdict validation (SPEC.md §6.3 and §7.3).

The OpenAI call and retrieval happen in the worker.  This module deliberately
does neither: it accepts only the already-returned judge payload and the exact
paragraphs supplied to that judge, then enforces Pincite's evidence and
confidence rules deterministically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
from pathlib import Path
from typing import Any

import yaml

from . import _find_threshold_config


class PropositionVerdict(StrEnum):
    """The proposition verdict vocabulary defined in SPEC.md §6.3."""

    SUPPORTS = "SUPPORTS"
    PARTIAL = "PARTIAL"
    CONTRADICTS = "CONTRADICTS"
    NOT_ADDRESSED = "NOT_ADDRESSED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class JudgeParagraph:
    """A paragraph that was actually supplied to the proposition judge."""

    para_id: str
    opinion_part: str
    text: str


@dataclass(frozen=True, slots=True)
class PropositionThresholds:
    """Committed P2 confidence and retrieval thresholds."""

    confidence_min: float
    contradicts_confidence_min: float
    top_k_paragraphs: int


@dataclass(frozen=True, slots=True)
class PropositionCheckResult:
    """A validated proposition finding, safe for persistence and display."""

    verdict: PropositionVerdict
    confidence: float
    rationale: str
    cited_paragraph_ids: tuple[str, ...]
    notes: tuple[str, ...]
    reason: str | None = None


_JUDGE_VERDICTS = frozenset(
    {
        PropositionVerdict.SUPPORTS.value,
        PropositionVerdict.PARTIAL.value,
        PropositionVerdict.CONTRADICTS.value,
        PropositionVerdict.NOT_ADDRESSED.value,
    }
)
_NON_MAJORITY_PARTS = frozenset({"dissent", "concurrence"})
_MAX_RATIONALE_CHARS = 300


def load_proposition_thresholds(path: str | Path | None = None) -> PropositionThresholds:
    """Load the committed P2 thresholds without introducing another config path."""

    config_path = Path(path) if path is not None else _find_threshold_config()
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    proposition = config["proposition_support"]
    retrieval = config["retrieval"]
    return PropositionThresholds(
        confidence_min=float(proposition["confidence_min"]),
        contradicts_confidence_min=float(proposition["contradicts_confidence_min"]),
        top_k_paragraphs=int(retrieval["top_k_paragraphs"]),
    )


def evaluate_proposition(
    raw_judge_output: Mapping[str, Any] | object,
    retrieved: Sequence[JudgeParagraph],
    thresholds: PropositionThresholds | None = None,
) -> PropositionCheckResult:
    """Validate a judge response against its supplied evidence.

    Any malformed output, invalid evidence ID, missing required evidence, or
    insufficient confidence becomes ``UNVERIFIABLE``.  The validator never
    attempts to infer legal meaning itself and never makes a network call.
    """

    threshold_values = thresholds or load_proposition_thresholds()
    if not isinstance(raw_judge_output, Mapping):
        return _unverifiable("INVALID_JUDGE_OUTPUT")

    verdict_value = raw_judge_output.get("verdict")
    if not isinstance(verdict_value, str) or verdict_value not in _JUDGE_VERDICTS:
        return _unverifiable("INVALID_VERDICT")

    rationale = raw_judge_output.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > _MAX_RATIONALE_CHARS:
        return _unverifiable("INVALID_RATIONALE")

    confidence = raw_judge_output.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _unverifiable("INVALID_CONFIDENCE")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return _unverifiable("INVALID_CONFIDENCE")

    cited_ids = _parse_cited_ids(raw_judge_output.get("cited_paragraph_ids"))
    if cited_ids is None:
        return _unverifiable("INVALID_CITED_PARAGRAPH_IDS", confidence=confidence)

    verdict = PropositionVerdict(verdict_value)
    if verdict is not PropositionVerdict.NOT_ADDRESSED and not cited_ids:
        return _unverifiable("MISSING_EVIDENCE", confidence=confidence)

    retrieved_by_id = {paragraph.para_id: paragraph for paragraph in retrieved}
    if any(paragraph_id not in retrieved_by_id for paragraph_id in cited_ids):
        return _unverifiable("INVALID_EVIDENCE_ID", confidence=confidence)

    if confidence < threshold_values.confidence_min:
        return _unverifiable("LOW_CONFIDENCE", confidence=confidence)
    if (
        verdict is PropositionVerdict.CONTRADICTS
        and confidence < threshold_values.contradicts_confidence_min
    ):
        return _unverifiable("CONTRADICTS_CONFIDENCE_TOO_LOW", confidence=confidence)

    notes: tuple[str, ...] = ()
    if verdict is PropositionVerdict.SUPPORTS and cited_ids:
        evidence_parts = {
            retrieved_by_id[paragraph_id].opinion_part.casefold().strip()
            for paragraph_id in cited_ids
        }
        if evidence_parts.issubset(_NON_MAJORITY_PARTS):
            verdict = PropositionVerdict.PARTIAL
            notes = ("NON_MAJORITY_SUPPORT",)

    return PropositionCheckResult(
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        cited_paragraph_ids=cited_ids,
        notes=notes,
    )


def _parse_cited_ids(value: object) -> tuple[str, ...] | None:
    """Accept only JSON-schema compatible, non-duplicate string ID arrays."""

    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    ids = tuple(value)
    return ids if len(set(ids)) == len(ids) else None


def _unverifiable(reason: str, *, confidence: float = 0.0) -> PropositionCheckResult:
    return PropositionCheckResult(
        verdict=PropositionVerdict.UNVERIFIABLE,
        confidence=confidence,
        rationale="",
        cited_paragraph_ids=(),
        notes=(),
        reason=reason,
    )


__all__ = [
    "JudgeParagraph",
    "PropositionCheckResult",
    "PropositionThresholds",
    "PropositionVerdict",
    "evaluate_proposition",
    "load_proposition_thresholds",
]
