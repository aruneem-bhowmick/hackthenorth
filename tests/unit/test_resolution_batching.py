from types import SimpleNamespace
import uuid

from worker.courtlistener import CitationLookup
from worker.tasks import (
    _batch_text,
    _citation_batches,
    _citation_asserted_year,
    _disambiguate_clusters,
    _investigator_context,
    _lookup_payloads_for_batch,
)


def _citation(raw_text: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), raw_text=raw_text, normalized=raw_text)


def test_batch_text_preserves_each_citation_offset() -> None:
    first = _citation("347 U.S. 483")
    second = _citation("576 U.S. 644")

    text, spans = _batch_text([first, second])

    assert text == "347 U.S. 483\n576 U.S. 644"
    assert spans[first.id] == (0, 12)
    assert spans[second.id] == (13, 25)


def test_lookup_payloads_map_by_batch_offset_and_preserve_missing_citation() -> None:
    first = _citation("347 U.S. 483")
    second = _citation("576 U.S. 644")
    _, spans = _batch_text([first, second])
    lookup = CitationLookup(
        citation="576 U.S. 644",
        normalized_citations=("576 U.S. 644",),
        start_index=13,
        end_index=25,
        status=200,
        error_message="",
        clusters=({"id": 1},),
    )

    payloads = _lookup_payloads_for_batch([first, second], spans, [lookup])

    assert payloads[second.id]["status"] == 200
    assert payloads[second.id]["clusters"] == [{"id": 1}]
    assert payloads[first.id]["status"] == 400


def test_citation_batches_keep_a_short_input_together() -> None:
    citations = [_citation("347 U.S. 483"), _citation("576 U.S. 644")]

    batches = _citation_batches(citations)

    assert batches == [citations]


def test_citation_batches_obey_shared_minute_quota() -> None:
    citations = [_citation(f"{number} U.S. 1") for number in range(61)]

    batches = _citation_batches(citations)

    assert [len(batch) for batch in batches] == [60, 1]


def test_ambiguous_clusters_are_disambiguated_only_by_exact_name_and_year() -> None:
    citation = SimpleNamespace(case_name="Lessee of Hyam v. Edwards", year_hint=1759)
    matching = {"case_name": "Lessee of Hyam v. Edwards", "date_filed": "1759-04-15"}
    same_name_wrong_year = {
        "case_name": "Lessee of Hyam v. Edwards",
        "date_filed": "1754-09-15",
    }
    other_case = {"case_name": "Anonymous", "date_filed": "1759-04-15"}

    assert _disambiguate_clusters(
        citation, [matching, same_name_wrong_year, other_case]
    ) == [matching]


def test_ambiguous_clusters_without_a_case_name_stay_ambiguous() -> None:
    citation = SimpleNamespace(case_name=None, year_hint=1759)
    assert _disambiguate_clusters(citation, [{"case_name": "Anonymous"}]) == []


def test_investigator_context_uses_the_configured_new_york_official_resolver() -> None:
    citation = SimpleNamespace(
        raw_text="Day v. Plumber's Shop & Assoc. LLC, 2025 NY Slip Op 51938(U)",
        case_name="Day v. Plumber's Shop & Assoc. LLC",
        court_hint=None,
        year_hint=2025,
    )

    context = _investigator_context(citation)

    assert context.official_search_domain == "www.nycourts.gov"
    assert [candidate.url for candidate in context.direct_official_candidates] == [
        "https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm"
    ]


def test_investigator_context_uses_one_explicit_citation_year_when_parser_omits_it() -> (
    None
):
    """Reporter-only citations still require source-side year corroboration."""

    citation = SimpleNamespace(
        raw_text="2025 NY Slip Op 51938(U)",
        case_name="Day v. Plumber's Shop & Assoc. LLC",
        court_hint=None,
        year_hint=None,
    )

    context = _investigator_context(citation)

    assert context.year == 2025
    assert [candidate.url for candidate in context.direct_official_candidates] == [
        "https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm"
    ]


def test_citation_asserted_year_rejects_an_ambiguous_or_missing_value() -> None:
    assert _citation_asserted_year("2025 NY Slip Op 51938(U)") == 2025
    assert _citation_asserted_year("2024 and 2025 NY Slip Op") is None
    assert _citation_asserted_year("347 U.S. 483") is None
