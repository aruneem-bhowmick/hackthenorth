from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.runner import _default_output, load_job_references, score_evaluation


def _label(existence: str, proposition: str = "MISREPRESENTED") -> dict:
    return {
        "brief": "pilot",
        "citations": [
            {
                "citation": "Example v. Example, 1 F.4th 2",
                "expected": {"existence": existence, "quote_or_proposition": proposition},
            }
        ],
    }


def test_runner_reports_per_check_precision_recall_and_abstentions() -> None:
    labels = {"pilot": _label("NOT_FOUND_ANYWHERE")}
    observations = {
        "pilot": [
            {
                "normalized": "Example v. Example, 1 F.4th 2",
                "findings": [
                    {"check": "existence", "verdict": "NOT_FOUND_ANYWHERE"},
                    {"check": "quote", "verdict": "NOT_FOUND_IN_SOURCE"},
                    {"check": "proposition", "verdict": "UNVERIFIABLE"},
                ],
            }
        ]
    }

    result = score_evaluation(labels, observations)

    assert result["dataset"]["briefs"] == 1
    assert result["metrics"]["existence"]["precision"] == 1.0
    assert result["metrics"]["quote"]["recall"] == 1.0
    assert result["metrics"]["proposition"]["abstentions"] == 1
    assert "Pilot-scale" in result["dataset"]["description"]


def test_runner_is_deterministic_for_unchanged_persisted_observations() -> None:
    labels = {"pilot": _label("VERIFIED", "ACCURATE")}
    observations = {
        "pilot": [
            {
                "raw_text": "Example v. Example, 1 F.4th 2",
                "findings": [
                    {"check": "existence", "verdict": "VERIFIED"},
                    {"check": "quote", "verdict": "VERBATIM"},
                    {"check": "proposition", "verdict": "SUPPORTS"},
                ],
            }
        ]
    }

    assert score_evaluation(labels, observations) == score_evaluation(labels, observations)


def test_runner_matches_a_reporter_label_to_a_full_persisted_citation() -> None:
    labels = {"pilot": _label("NOT_FOUND_ANYWHERE")}
    labels["pilot"]["citations"][0]["citation"] = "337 F.R.D. 659"
    observations = {
        "pilot": [
            {
                "normalized": "fuller v. athleta, llc, 337 f.r.d. 659, 663 (n.d. cal. 2020)",
                "findings": [{"check": "existence", "verdict": "NOT_FOUND_ANYWHERE"}],
            }
        ]
    }

    result = score_evaluation(labels, observations)

    assert result["metrics"]["existence"]["labelled"] == 1
    assert result["metrics"]["existence"]["true_positives"] == 1


def test_runner_uses_existing_upload_volume_for_production_aggregate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))

    assert _default_output(tmp_path) == tmp_path / "uploads" / "eval-results" / "latest.json"


def test_runner_derives_stable_label_ids_from_baseline_display_names(tmp_path: Path) -> None:
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    results.joinpath("pilot.md").write_text(
        "| Fivehouse (D.E. 86) | `b5aba23a-dae2-4040-b8e7-566d01fe7e6a` |\n"
        "| Cole (D.E. 25) | `2f65174b-a7c0-4f9d-b247-4fcb85a77ac0` |\n",
        encoding="utf-8",
    )

    assert load_job_references(tmp_path) == {
        "fivehouse": "b5aba23a-dae2-4040-b8e7-566d01fe7e6a",
        "cole": "2f65174b-a7c0-4f9d-b247-4fcb85a77ac0",
    }
