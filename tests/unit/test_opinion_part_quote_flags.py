from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.tasks import _quote_check


@pytest.mark.asyncio
async def test_worker_quote_check_flags_a_quote_from_a_dissent() -> None:
    """FR-OPP-001: the persisted worker result, not just the pure checker, flags it."""

    findings = await _quote_check(
        SimpleNamespace(pinpoint=None),
        SimpleNamespace(id="source-1"),
        [SimpleNamespace(quote_text="The statute does not authorize this result.")],
        [
            SimpleNamespace(
                id="paragraph-1",
                text="The statute does not authorize this result.",
                page=45,
                opinion_part="dissent",
            )
        ],
        semantic_check_enabled=False,
    )

    assert len(findings) == 1
    assert "QUOTED_FROM_DISSENT" in findings[0][2]
