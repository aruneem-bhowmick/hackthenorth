from __future__ import annotations

import math

import pytest

from verdicts.proposition import (
    JudgeParagraph,
    PropositionThresholds,
    PropositionVerdict,
    evaluate_proposition,
    load_proposition_thresholds,
)


THRESHOLDS = PropositionThresholds(
    confidence_min=0.6,
    contradicts_confidence_min=0.75,
    top_k_paragraphs=6,
)
PARAGRAPHS = (
    JudgeParagraph("src_1:p1", "majority", "The majority discusses standing."),
    JudgeParagraph("src_1:p2", "dissent", "The dissent would dismiss the case."),
    JudgeParagraph("src_1:p3", "concurrence", "The concurrence addresses remedy."),
    JudgeParagraph("src_1:p4", "unknown", "The source did not distinguish this part."),
)


def payload(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "verdict": "SUPPORTS",
        "cited_paragraph_ids": ["src_1:p1"],
        "rationale": "The cited paragraph directly supports the proposition.",
        "confidence": 0.8,
    }
    result.update(overrides)
    return result


def test_loads_committed_proposition_and_retrieval_thresholds():
    thresholds = load_proposition_thresholds()

    assert thresholds == THRESHOLDS


@pytest.mark.parametrize(
    ("verdict", "cited_ids"),
    [
        ("SUPPORTS", ["src_1:p1"]),
        ("PARTIAL", ["src_1:p1"]),
        ("CONTRADICTS", ["src_1:p1"]),
        ("NOT_ADDRESSED", []),
    ],
)
def test_accepts_each_schema_defined_judge_verdict(verdict: str, cited_ids: list[str]):
    confidence = 0.8
    checked = evaluate_proposition(payload(verdict=verdict, cited_paragraph_ids=cited_ids, confidence=confidence), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict(verdict)
    assert checked.confidence == confidence
    assert checked.reason is None


def test_not_addressed_may_cite_a_retrieved_paragraph():
    checked = evaluate_proposition(payload(verdict="NOT_ADDRESSED", cited_paragraph_ids=["src_1:p1"]), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.NOT_ADDRESSED
    assert checked.cited_paragraph_ids == ("src_1:p1",)


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"verdict": "UNVERIFIABLE"}, "INVALID_VERDICT"),
        ({"verdict": "OTHER"}, "INVALID_VERDICT"),
        ({"rationale": ""}, "INVALID_RATIONALE"),
        ({"rationale": "x" * 301}, "INVALID_RATIONALE"),
        ({"confidence": "0.8"}, "INVALID_CONFIDENCE"),
        ({"confidence": True}, "INVALID_CONFIDENCE"),
        ({"confidence": math.nan}, "INVALID_CONFIDENCE"),
        ({"confidence": 1.1}, "INVALID_CONFIDENCE"),
        ({"cited_paragraph_ids": "src_1:p1"}, "INVALID_CITED_PARAGRAPH_IDS"),
        ({"cited_paragraph_ids": ["src_1:p1", "src_1:p1"]}, "INVALID_CITED_PARAGRAPH_IDS"),
        ({"cited_paragraph_ids": ["src_1:p1", 4]}, "INVALID_CITED_PARAGRAPH_IDS"),
    ],
)
def test_rejects_malformed_schema_output(changed: dict[str, object], reason: str):
    checked = evaluate_proposition(payload(**changed), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.UNVERIFIABLE
    assert checked.reason == reason


def test_rejects_non_mapping_judge_output():
    checked = evaluate_proposition("not JSON", PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.UNVERIFIABLE
    assert checked.reason == "INVALID_JUDGE_OUTPUT"


@pytest.mark.parametrize(
    ("verdict", "cited_ids", "reason"),
    [
        ("SUPPORTS", [], "MISSING_EVIDENCE"),
        ("PARTIAL", [], "MISSING_EVIDENCE"),
        ("CONTRADICTS", [], "MISSING_EVIDENCE"),
        ("SUPPORTS", ["missing"], "INVALID_EVIDENCE_ID"),
        ("NOT_ADDRESSED", ["missing"], "INVALID_EVIDENCE_ID"),
    ],
)
def test_enforces_evidence_requirements(verdict: str, cited_ids: list[str], reason: str):
    checked = evaluate_proposition(payload(verdict=verdict, cited_paragraph_ids=cited_ids), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.UNVERIFIABLE
    assert checked.reason == reason


def test_below_general_confidence_threshold_is_unverifiable():
    checked = evaluate_proposition(payload(confidence=0.599), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.UNVERIFIABLE
    assert checked.confidence == 0.599
    assert checked.reason == "LOW_CONFIDENCE"


@pytest.mark.parametrize("confidence", [0.6, 0.749])
def test_contradicts_needs_its_higher_confidence_bar(confidence: float):
    checked = evaluate_proposition(payload(verdict="CONTRADICTS", confidence=confidence), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.UNVERIFIABLE
    assert checked.reason == "CONTRADICTS_CONFIDENCE_TOO_LOW"


def test_contradicts_at_higher_confidence_bar_is_accepted():
    checked = evaluate_proposition(payload(verdict="CONTRADICTS", confidence=0.75), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.CONTRADICTS


def test_supports_based_only_on_dissent_and_concurrence_is_downgraded():
    checked = evaluate_proposition(
        payload(cited_paragraph_ids=["src_1:p2", "src_1:p3"]),
        PARAGRAPHS,
        THRESHOLDS,
    )

    assert checked.verdict is PropositionVerdict.PARTIAL
    assert checked.notes == ("NON_MAJORITY_SUPPORT",)


@pytest.mark.parametrize("paragraph_id", ["src_1:p1", "src_1:p4"])
def test_majority_or_unknown_evidence_does_not_trigger_non_majority_downgrade(paragraph_id: str):
    checked = evaluate_proposition(payload(cited_paragraph_ids=[paragraph_id]), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.SUPPORTS
    assert checked.notes == ()


def test_property_every_injected_out_of_set_id_is_unverifiable():
    """Required §11.1 property-style check without a new test dependency."""

    for index in range(100):
        checked = evaluate_proposition(
            payload(cited_paragraph_ids=[f"attacker-controlled-{index}"], confidence=0.95),
            PARAGRAPHS,
            THRESHOLDS,
        )

        assert checked.verdict is PropositionVerdict.UNVERIFIABLE
        assert checked.reason == "INVALID_EVIDENCE_ID"


def test_validator_has_no_network_side_effects(monkeypatch: pytest.MonkeyPatch):
    """The pure validator must not require clients or attempt I/O."""

    def fail_config_lookup():
        raise AssertionError("explicit thresholds mean no configuration I/O")

    monkeypatch.setattr("verdicts.proposition._find_threshold_config", fail_config_lookup)
    checked = evaluate_proposition(payload(), PARAGRAPHS, THRESHOLDS)

    assert checked.verdict is PropositionVerdict.SUPPORTS
