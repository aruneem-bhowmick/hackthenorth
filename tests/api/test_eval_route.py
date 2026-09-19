from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.routes import eval as evaluation


@pytest.mark.asyncio
async def test_eval_route_prefers_persisted_aggregate_result(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    upload_dir = tmp_path / "uploads"
    result = upload_dir / "eval-results" / "latest.json"
    result.parent.mkdir(parents=True)
    result.write_text(
        json.dumps(
            {
                "dataset": {
                    "description": "pilot",
                    "job_references": {"pilot": "00000000-0000-0000-0000-000000000000"},
                },
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(evaluation, "get_settings", lambda: SimpleNamespace(uploads_dir=str(upload_dir)))

    payload = await evaluation.get_latest_evaluation()

    assert payload["dataset"]["description"] == "pilot"
    assert "job_references" not in payload["dataset"]
