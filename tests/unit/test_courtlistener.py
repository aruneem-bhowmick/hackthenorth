import httpx
import pytest

from worker.courtlistener import (
    CourtListenerClient,
    CourtListenerUnavailable,
    MAX_CITATIONS_PER_REQUEST,
    MAX_TEXT_CHARS_PER_REQUEST,
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_lookup_posts_text_and_maps_a_found_citation() -> None:
    async def acquire(count: int) -> None:
        assert count == 1

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token secret"
        assert request.url.path.endswith("/citation-lookup/")
        return httpx.Response(
            200,
            json=[
                {
                    "citation": "576 U.S. 644",
                    "normalized_citations": ["576 U.S. 644"],
                    "start_index": 0,
                    "end_index": 12,
                    "status": 200,
                    "error_message": "",
                    "clusters": [{"id": 10, "case_name": "Obergefell v. Hodges"}],
                }
            ],
        )

    async with _client(handler) as http_client:
        result = await CourtListenerClient(
            "secret", client=http_client, acquire_rate_limit=acquire
        ).lookup_text("576 U.S. 644", expected_citations=1)

    assert result[0].p1_verdict == "VERIFIED"
    assert result[0].clusters[0]["id"] == 10


@pytest.mark.asyncio
async def test_rate_limit_retries_then_returns_response() -> None:
    attempts = 0
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"wait_until": "2099-01-01T00:00:00Z"})
        return httpx.Response(200, json=[])

    async with _client(handler) as http_client:
        result = await CourtListenerClient("secret", client=http_client, sleep=sleep).lookup_text(
            "", expected_citations=0
        )

    assert result == []
    assert waits == [1]


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_is_upstream_unavailable() -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    async with _client(lambda _request: httpx.Response(429)) as http_client:
        with pytest.raises(CourtListenerUnavailable, match="rate limited"):
            await CourtListenerClient(
                "secret", client=http_client, max_retries=1, sleep=no_sleep
            ).lookup_text("", expected_citations=0)


@pytest.mark.parametrize(
    ("text", "count"),
    [("x" * (MAX_TEXT_CHARS_PER_REQUEST + 1), 0), ("", MAX_CITATIONS_PER_REQUEST + 1)],
    ids=("text_over_limit", "citation_count_over_limit"),
)
@pytest.mark.asyncio
async def test_lookup_rejects_batches_outside_upstream_limits(text: str, count: int) -> None:
    async with _client(lambda _request: httpx.Response(200, json=[])) as http_client:
        with pytest.raises(ValueError):
            await CourtListenerClient("secret", client=http_client).lookup_text(
                text, expected_citations=count
            )


def test_unrecognized_status_has_no_unapproved_p1_verdict() -> None:
    from worker.courtlistener import CitationLookup

    lookup = CitationLookup("33 Umbrella 422", (), 0, 15, 400, "", ())
    with pytest.raises(ValueError, match="no approved P1 verdict"):
        _ = lookup.p1_verdict
