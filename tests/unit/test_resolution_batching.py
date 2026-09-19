from types import SimpleNamespace
import uuid

from worker.courtlistener import CitationLookup
from worker.tasks import _batch_text, _citation_batches, _lookup_payloads_for_batch


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
