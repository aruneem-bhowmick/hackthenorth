"""Run P4's labelled pilot evaluation against already-persisted jobs.

This intentionally never uploads or reprocesses a brief.  It reads the
normal pipeline's durable findings, compares them with the hand labels in
``eval/labels``, and writes only aggregate metrics to ``eval/results``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db import Citation  # noqa: E402


PILOT_DESCRIPTION = (
    "Pilot-scale labelled set based on public briefs and court orders. It is not "
    "the full SPEC.md §12 target set, and results describe only the referenced "
    "persisted jobs; no pipeline work is rerun while evaluating."
)
_ISSUE_QUOTE = {"ALTERED", "PARAPHRASE_IN_QUOTES", "NOT_FOUND_IN_SOURCE"}
_ISSUE_PROPOSITION = {"PARTIAL", "CONTRADICTS", "NOT_ADDRESSED"}
_EXISTENCE_GOOD = {"VERIFIED", "VERIFIED_OFFICIAL"}


def load_labels(labels_dir: Path) -> dict[str, dict[str, Any]]:
    """Load the hand-authored YAML answer keys without modifying them."""

    labels: dict[str, dict[str, Any]] = {}
    for path in sorted(labels_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("brief"), str):
            raise ValueError(f"invalid evaluation label file: {path}")
        labels[payload["brief"].casefold()] = payload
    return labels


def load_job_references(root: Path) -> dict[str, str]:
    """Read optional explicit refs, then reuse the existing pilot baseline table."""

    explicit = root / "eval" / "job_refs.json"
    if explicit.exists():
        payload = json.loads(explicit.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {str(key).casefold(): str(value) for key, value in payload.items()}
    references: dict[str, str] = {}
    for result_file in sorted((root / "eval" / "results").glob("*.md")):
        for brief, job_id in re.findall(r"\|\s*([^|]+?)\s*\|\s*`([0-9a-f-]{36})`", result_file.read_text(encoding="utf-8"), re.IGNORECASE):
            # The durable P4 baseline table records friendly display labels
            # such as ``Fivehouse (D.E. 86)`` while answer keys use the stable
            # short identifier ``fivehouse``.  Derive that identifier without
            # requiring a second hand-maintained reference file.
            references[brief.strip().split(" (", 1)[0].casefold()] = job_id
    return references


def score_evaluation(
    labels: Mapping[str, Mapping[str, Any]],
    observations_by_brief: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compute deterministic issue-detection precision/recall by check.

    Existence's positive class is a labelled citation whose expected outcome is
    not verified. Quote/proposition positive classes are labelled citations
    whose use is marked ``MISREPRESENTED``. ``UNVERIFIABLE`` is reported as an
    abstention rather than treated as a successful detection.
    """

    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for brief, label in labels.items():
        observations = list(observations_by_brief.get(brief, []))
        citations = label.get("citations", [])
        if not isinstance(citations, list):
            continue
        for expected_row in citations:
            if not isinstance(expected_row, dict) or not isinstance(expected_row.get("citation"), str):
                continue
            observed = _observations_for_citation(
                observations, expected_row["citation"]
            )
            expected = expected_row.get("expected", {})
            if not isinstance(expected, dict):
                continue
            _score_existence(tally["existence"], expected.get("existence"), observed)
            issue = expected.get("quote_or_proposition") == "MISREPRESENTED"
            if "quote_or_proposition" in expected:
                _score_issue_check(tally["quote"], issue, _first_verdict(observed, "quote"), _ISSUE_QUOTE)
                _score_issue_check(
                    tally["proposition"], issue, _first_verdict(observed, "proposition"), _ISSUE_PROPOSITION
                )
    metrics = {check: _metrics(counts) for check, counts in sorted(tally.items())}
    return {
        "dataset": {
            "description": PILOT_DESCRIPTION,
            "briefs": len(labels),
            "labelled_citations": sum(len(label.get("citations", [])) for label in labels.values()),
        },
        "metric_definition": {
            "existence": "positive means an expected non-verified existence outcome",
            "quote": "positive means an expected quotation discrepancy",
            "proposition": "positive means an expected proposition-support discrepancy",
            "abstentions": "UNVERIFIABLE, missing, or non-terminal findings are reported separately",
        },
        "metrics": metrics,
    }


def _observations_for_citation(
    observations: Iterable[Mapping[str, Any]], expected_citation: str
) -> list[Mapping[str, Any]]:
    """Return persisted observations for one stable label citation.

    Labels deliberately identify an authority by its reporter citation, while
    extracted records retain the parties, pinpoints, and parentheticals from
    the brief.  Exact equality would therefore turn a valid production result
    into a false abstention.  Containment is safe here because normalisation
    preserves reporter punctuation and page numbers (for example,
    ``337f.r.d.659``), and the direction is label -> persisted citation only.
    """

    expected_key = _key(expected_citation)
    if not expected_key:
        return []
    matches: list[Mapping[str, Any]] = []
    for item in observations:
        for field in ("normalized", "raw_text"):
            candidate = item.get(field)
            if isinstance(candidate, str) and expected_key in _key(candidate):
                matches.append(item)
                break
    return matches


def _score_existence(counts: dict[str, int], expected: object, observations: list[Mapping[str, Any]]) -> None:
    if not isinstance(expected, str):
        return
    predicted = _first_verdict(observations, "existence")
    expected_issue = expected not in _EXISTENCE_GOOD
    _score_binary(counts, expected_issue, predicted is not None and predicted not in _EXISTENCE_GOOD, predicted)
    counts["exact_matches"] += int(predicted == expected)


def _score_issue_check(
    counts: dict[str, int], expected_issue: bool, predicted: str | None, issue_verdicts: set[str]
) -> None:
    _score_binary(counts, expected_issue, predicted in issue_verdicts if predicted else False, predicted)


def _score_binary(counts: dict[str, int], expected_issue: bool, predicted_issue: bool, predicted: str | None) -> None:
    counts["labelled"] += 1
    if predicted is None or predicted in {"UNVERIFIABLE", "SOURCE_UNAVAILABLE", "PENDING"}:
        counts["abstentions"] += 1
        if expected_issue:
            counts["false_negatives"] += 1
        return
    counts["scored"] += 1
    if expected_issue and predicted_issue:
        counts["true_positives"] += 1
    elif expected_issue:
        counts["false_negatives"] += 1
    elif predicted_issue:
        counts["false_positives"] += 1
    else:
        counts["true_negatives"] += 1


def _metrics(counts: Mapping[str, int]) -> dict[str, Any]:
    tp, fp, fn = (counts.get(key, 0) for key in ("true_positives", "false_positives", "false_negatives"))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "precision": precision,
        "recall": recall,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": counts.get("true_negatives", 0),
        "abstentions": counts.get("abstentions", 0),
        "labelled": counts.get("labelled", 0),
        "scored": counts.get("scored", 0),
        **({"exact_matches": counts.get("exact_matches", 0)} if "exact_matches" in counts else {}),
    }


def _first_verdict(observations: Iterable[Mapping[str, Any]], check: str) -> str | None:
    for observation in observations:
        findings = observation.get("findings", [])
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if isinstance(finding, Mapping) and finding.get("check") == check and isinstance(finding.get("verdict"), str):
                return finding["verdict"]
    return None


def _key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


async def run_evaluation(
    database_url: str,
    *,
    root: Path = ROOT,
    output: Path | None = None,
    job_references: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read referenced jobs and write aggregate-only results deterministically."""

    labels = load_labels(root / "eval" / "labels")
    references = dict(job_references or load_job_references(root))
    missing = sorted(set(labels) - set(references))
    if missing:
        raise ValueError("missing persisted job references for: " + ", ".join(missing))
    engine = create_async_engine(_asyncpg_url(database_url))
    observations_by_brief: dict[str, list[dict[str, Any]]] = {}
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            for brief, job_id in sorted(references.items()):
                rows = await session.execute(
                    select(Citation)
                    .where(Citation.job_id == uuid.UUID(job_id))
                    .options(selectinload(Citation.findings))
                )
                citations = rows.scalars().unique().all()
                observations_by_brief[brief] = [
                    {
                        "raw_text": citation.raw_text,
                        "normalized": citation.normalized,
                        "findings": [{"check": finding.check, "verdict": finding.verdict} for finding in citation.findings],
                    }
                    for citation in citations
                ]
    finally:
        await engine.dispose()
    result = score_evaluation(labels, observations_by_brief)
    target = output or _default_output(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _asyncpg_url(url: str) -> str:
    return "postgresql+asyncpg://" + url.removeprefix("postgresql://") if url.startswith("postgresql://") else url


def _default_output(root: Path) -> Path:
    """Use Railway's existing durable upload volume when it is configured."""

    uploads_dir = os.environ.get("UPLOADS_DIR")
    return Path(uploads_dir) / "eval-results" / "latest.json" if uploads_dir else root / "eval" / "results" / "latest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score persisted PinCite pilot jobs against hand labels.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--job", action="append", default=[], metavar="BRIEF=UUID")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    references: dict[str, str] = {}
    for item in args.job:
        if "=" not in item:
            parser.error("--job must be BRIEF=UUID")
        brief, job_id = item.split("=", 1)
        references[brief.casefold()] = job_id
    asyncio.run(run_evaluation(args.database_url, output=args.output, job_references=references or None))


if __name__ == "__main__":
    main()
