from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.retrieval_ablation import score_retrieval_ablation


def _fixture() -> dict:
    return {
        "schema_version": 1,
        "dataset": {"id": "tiny-public-fixture", "description": "Synthetic test corpus only."},
        "top_k": 1,
        "rerank_candidate_k": 2,
        "corpus": [
            {"id": "p-good", "text": "administrative record completeness presumption"},
            {"id": "p-lexical", "text": "administrative record record record irrelevant"},
            {"id": "p-other", "text": "unrelated maritime jurisdiction"},
        ],
        "queries": [
            {
                "id": "q-1",
                "text": "administrative record presumption",
                "relevant_paragraph_ids": ["p-good"],
                "semantic_scores": {"p-good": 0.99, "p-lexical": 0.3, "p-other": 0.0},
                "rerank_scores": {"p-good": 0.98, "p-lexical": 0.2, "p-other": 0.1},
            }
        ],
    }


def test_offline_ablation_emits_all_modes_and_honest_retrieval_metrics() -> None:
    result = score_retrieval_ablation(_fixture())

    assert set(result["retrieval_ablation"]) == {"bm25", "hybrid", "hybrid_rerank"}
    assert result["retrieval_ablation"]["hybrid"]["metric_kind"] == "retrieval"
    assert result["retrieval_ablation"]["hybrid"]["recall"] == 1.0
    assert "accuracy" not in result["retrieval_ablation"]["hybrid"]
    assert result["retrieval_ablation_metadata"]["judge_metrics_included"] is False
    assert result["retrieval_ablation_metadata"]["contract_version"] == 1


def test_offline_ablation_requires_explicit_complete_scores() -> None:
    fixture = _fixture()
    del fixture["queries"][0]["semantic_scores"]["p-other"]

    with pytest.raises(ValueError, match="score every corpus paragraph"):
        score_retrieval_ablation(fixture)


def test_offline_ablation_rejects_gold_ids_outside_the_frozen_corpus() -> None:
    fixture = _fixture()
    fixture["queries"][0]["relevant_paragraph_ids"] = ["invented"]

    with pytest.raises(ValueError, match="outside corpus"):
        score_retrieval_ablation(fixture)
