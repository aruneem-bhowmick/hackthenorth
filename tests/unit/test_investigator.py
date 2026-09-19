from __future__ import annotations

from collections.abc import Sequence
import asyncio

import pytest

from investigator.agent import (
    CitationContext,
    DirectOfficialCandidate,
    ExtractedCandidate,
    InvestigationBudget,
    InvestigationVerdict,
    SearchResult,
    run_investigation,
)
from investigator.domains import is_allowlisted, is_denylisted, may_fetch


class FakeSession:
    def __init__(
        self, results: Sequence[SearchResult], extracted: dict[str, ExtractedCandidate]
    ) -> None:
        self.live_view_url = "https://live.example/session"
        self.session_ref = "session-test"
        self._results = results
        self._extracted = extracted
        self.queries: list[str] = []
        self.fetched: list[str] = []
        self.closed = False

    async def search(self, query: str) -> Sequence[SearchResult]:
        self.queries.append(query)
        return self._results

    async def fetch_and_extract(self, url: str) -> ExtractedCandidate:
        self.fetched.append(url)
        return self._extracted[url]

    async def close(self) -> None:
        self.closed = True


async def _factory(session: FakeSession, _: str) -> FakeSession:
    return session


def _context() -> CitationContext:
    return CitationContext(
        case_name="Example v. State", court="N.D. Example", year=2024
    )


def _matches(candidate: ExtractedCandidate, context: CitationContext) -> bool:
    return candidate.case_name == context.case_name and candidate.court == context.court


def test_domain_policy_uses_globs_and_denylist_precedence() -> None:
    allowlist = ("*.uscourts.gov", "ecfr.gov")
    denylist = ("ecf.*", "scholar.google.com")

    assert is_allowlisted("https://www.uscourts.gov/opinions/a", allowlist)
    assert is_allowlisted("https://www.ecfr.gov/current/title-1", allowlist)
    assert is_denylisted("https://ecf.uscourts.gov/filing", denylist)
    assert not may_fetch(
        "https://ecf.uscourts.gov/filing",
        allowlist=("*.uscourts.gov",),
        denylist=denylist,
    )
    assert not may_fetch(
        "file:///local/opinion", allowlist=allowlist, denylist=denylist
    )
    assert not may_fetch(
        "https://account:secret@www.uscourts.gov/opinion",
        allowlist=allowlist,
        denylist=denylist,
    )


@pytest.mark.asyncio
async def test_official_match_fetches_only_allowlisted_candidates_and_reports_live_view() -> (
    None
):
    official = "https://opinions.uscourts.gov/example"
    session = FakeSession(
        results=(
            SearchResult("https://scholar.google.com/example", "Example v. State 2024"),
            SearchResult(
                "https://blog.example/example", "Example v. State — N.D. Example 2024"
            ),
            SearchResult(official, "Official opinion"),
        ),
        extracted={
            official: ExtractedCandidate(
                "Example v. State", court="N.D. Example", decision_date="2024-01-02"
            )
        },
    )
    live_views: list[str | None] = []

    outcome = await run_investigation(
        "explicit-key",
        _context(),
        InvestigationBudget(max_searches=1, max_fetches=5, max_wall_time_seconds=90),
        allowlist=("*.uscourts.gov",),
        denylist=("scholar.google.com",),
        match_candidate=_matches,
        session_factory=lambda key: _factory(session, key),
        on_live_view=live_views.append,
    )

    assert outcome.verdict is InvestigationVerdict.VERIFIED_OFFICIAL
    assert session.fetched == [official]
    assert live_views == ["https://live.example/session"]
    assert outcome.session_ref == "session-test"
    assert session.closed


@pytest.mark.asyncio
async def test_nonofficial_matching_mention_is_weak_and_never_fetched() -> None:
    secondary = "https://news.example/example"
    session = FakeSession(
        results=(SearchResult(secondary, "Example v. State — N.D. Example, 2024"),),
        extracted={},
    )

    outcome = await run_investigation(
        "explicit-key",
        _context(),
        InvestigationBudget(max_searches=1, max_fetches=5, max_wall_time_seconds=90),
        allowlist=("*.uscourts.gov",),
        denylist=(),
        match_candidate=_matches,
        session_factory=lambda key: _factory(session, key),
    )

    assert outcome.verdict is InvestigationVerdict.WEAKLY_CORROBORATED
    assert outcome.secondary_mentions[0].url == secondary
    assert session.fetched == []


@pytest.mark.asyncio
async def test_fetch_budget_is_a_hard_limit() -> None:
    first = "https://opinions.uscourts.gov/first"
    second = "https://opinions.uscourts.gov/second"
    session = FakeSession(
        results=(SearchResult(first, "first"), SearchResult(second, "second")),
        extracted={
            first: ExtractedCandidate("different"),
            second: ExtractedCandidate("Example v. State", court="N.D. Example"),
        },
    )

    outcome = await run_investigation(
        "explicit-key",
        _context(),
        InvestigationBudget(max_searches=1, max_fetches=1, max_wall_time_seconds=90),
        allowlist=("*.uscourts.gov",),
        denylist=(),
        match_candidate=_matches,
        session_factory=lambda key: _factory(session, key),
    )

    assert outcome.verdict is InvestigationVerdict.NOT_FOUND_ANYWHERE
    assert outcome.budget_exhausted
    assert session.fetched == [first]


@pytest.mark.asyncio
async def test_upstream_failure_never_becomes_not_found_anywhere() -> None:
    class FailingSession(FakeSession):
        async def search(self, query: str) -> Sequence[SearchResult]:
            raise RuntimeError("search provider unavailable")

    session = FailingSession((), {})
    outcome = await run_investigation(
        "explicit-key",
        _context(),
        InvestigationBudget(max_searches=1, max_fetches=5, max_wall_time_seconds=90),
        allowlist=("*.uscourts.gov",),
        denylist=(),
        match_candidate=_matches,
        session_factory=lambda key: _factory(session, key),
    )

    assert outcome.verdict is InvestigationVerdict.UNVERIFIABLE


def test_official_search_query_replaces_the_third_generic_query() -> None:
    context = CitationContext(
        case_name="Example v. State",
        parties=("2024 Example Slip Op 1",),
        court="Example Court",
        year=2024,
        official_search_domain="opinions.example.gov",
    )
    from investigator.agent import _query_variants

    queries = _query_variants(context)
    assert len(queries) == 3
    assert queries[-1].startswith("site:opinions.example.gov ")


@pytest.mark.asyncio
async def test_search_timeout_respects_the_wall_clock_budget() -> None:
    class SlowSession(FakeSession):
        async def search(self, query: str) -> Sequence[SearchResult]:
            await asyncio.sleep(0.2)
            return ()

    session = SlowSession((), {})
    outcome = await run_investigation(
        "explicit-key",
        _context(),
        InvestigationBudget(max_searches=1, max_fetches=1, max_wall_time_seconds=0.01),
        allowlist=("*.uscourts.gov",),
        denylist=(),
        match_candidate=_matches,
        session_factory=lambda key: _factory(session, key),
    )
    assert outcome.budget_exhausted
    assert outcome.errors == ("search timed out",)


@pytest.mark.asyncio
async def test_specific_official_search_result_is_prioritized_before_fetch_budget() -> (
    None
):
    distractor = "https://www.nycourts.gov/reporter/3dseries/2025/2025_50001.htm"
    target = "https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm"

    class QuerySession(FakeSession):
        async def search(self, query: str) -> Sequence[SearchResult]:
            self.queries.append(query)
            if query.startswith("site:www.nycourts.gov"):
                return (
                    SearchResult(
                        target,
                        "Day v. Plumber's Shop & Assoc. LLC, 2025 NY Slip Op 51938(U)",
                    ),
                )
            return (SearchResult(distractor, "Other New York decision 2025"),)

    context = CitationContext(
        case_name="Day v. Plumber's Shop & Assoc. LLC",
        parties=("2025 NY Slip Op 51938(U)",),
        year=2025,
        official_search_domain="www.nycourts.gov",
    )
    session = QuerySession(
        (),
        {
            distractor: ExtractedCandidate("Other case", decision_date="2025-01-01"),
            target: ExtractedCandidate(
                "Day v. Plumber's Shop & Assoc. LLC", decision_date="2025-09-16"
            ),
        },
    )

    outcome = await run_investigation(
        "explicit-key",
        context,
        InvestigationBudget(max_searches=3, max_fetches=1, max_wall_time_seconds=90),
        allowlist=("www.nycourts.gov",),
        denylist=(),
        match_candidate=lambda candidate, _: candidate.case_name.startswith("Day v."),
        session_factory=lambda key: _factory(session, key),
    )

    assert outcome.verdict is InvestigationVerdict.VERIFIED_OFFICIAL
    assert session.fetched == [target]


@pytest.mark.asyncio
async def test_configured_direct_candidate_uses_an_existing_fetch_slot_after_search() -> (
    None
):
    target = "https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm"
    context = CitationContext(
        case_name="Day v. Plumber's Shop & Assoc. LLC",
        direct_official_candidates=(DirectOfficialCandidate(target, "ny_reporter_3d"),),
    )
    session = FakeSession(
        (),
        {
            target: ExtractedCandidate(
                "Day v. Plumber's Shop & Assoc. LLC", decision_date="2025-09-16"
            )
        },
    )

    outcome = await run_investigation(
        "explicit-key",
        context,
        InvestigationBudget(max_searches=3, max_fetches=1, max_wall_time_seconds=90),
        allowlist=("www.nycourts.gov",),
        denylist=(),
        match_candidate=lambda candidate, _: candidate.case_name.startswith("Day v."),
        session_factory=lambda key: _factory(session, key),
    )

    assert outcome.verdict is InvestigationVerdict.VERIFIED_OFFICIAL
    assert outcome.discovery_method == "official_citation_resolver:ny_reporter_3d"
    assert session.fetched == [target]
