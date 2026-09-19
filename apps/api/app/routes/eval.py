"""Read-only P4 evaluation-results endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config import get_settings

router = APIRouter(prefix="/api/eval", tags=["evaluation"])
_PACKAGED_LATEST_RESULT = Path(__file__).resolve().parents[4] / "eval" / "results" / "latest.json"


def _latest_result_paths() -> tuple[Path, Path]:
    """Prefer the existing persistent backend volume over immutable image data."""

    persistent = Path(get_settings().uploads_dir) / "eval-results" / "latest.json"
    return persistent, _PACKAGED_LATEST_RESULT


@router.get("/latest")
async def get_latest_evaluation() -> dict[str, Any]:
    """Return the latest aggregate-only pilot run; never trigger new pipeline work."""

    result_path = next((path for path in _latest_result_paths() if path.exists()), None)
    if result_path is None:
        raise HTTPException(
            404,
            detail={"error": {"code": "NOT_FOUND", "message": "no evaluation run has been generated yet"}},
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            500,
            detail={"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "evaluation results are unavailable"}},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(500, detail={"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "evaluation results are invalid"}})
    # An evaluation page reports aggregate metrics only.  Older local result
    # files may retain their source-job UUIDs for reruns; never publish those
    # identifiers through this endpoint because they are not an aggregate.
    dataset = payload.get("dataset")
    if isinstance(dataset, dict) and "job_references" in dataset:
        payload = dict(payload)
        payload["dataset"] = {
            key: value for key, value in dataset.items() if key != "job_references"
        }
    return payload
