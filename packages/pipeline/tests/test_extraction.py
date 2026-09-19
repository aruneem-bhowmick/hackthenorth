from __future__ import annotations

from pipeline.extraction import extract_citations
from pipeline.ingestion import normalise_page
from pipeline.models import AntecedentState, CitationKind, IngestedDocument, PageText


def document(*raw_pages: str) -> IngestedDocument:
    return IngestedDocument(
        tuple(PageText(number, raw, normalise_page(raw)) for number, raw in enumerate(raw_pages, 1))
    )


def test_extracts_full_short_id_and_supra_with_pinpoints_and_antecedents() -> None:
    citations = extract_citations(
        document(
            "Roe v. Wade, 410 U.S. 113, 155 (1973). "
            "Roe, 410 U.S. at 120. Id. at 121. Roe, supra, at 122."
        )
    )

    assert [item.kind for item in citations] == [
        CitationKind.FULL,
        CitationKind.SHORT,
        CitationKind.ID,
        CitationKind.SUPRA,
    ]
    assert citations[0].case_name == "Roe v. Wade"
    assert citations[0].year_hint == 1973
    assert [item.pinpoint for item in citations] == [("155",), ("120",), ("121",), ("122",)]
    assert [item.antecedent_id for item in citations[1:]] == [citations[0].id] * 3
    assert all(item.antecedent_state is AntecedentState.RESOLVED for item in citations[1:])


def test_unresolved_reference_is_stored_not_dropped() -> None:
    citations = extract_citations(document("Id. at 12. Smith, supra, at 4."))

    assert len(citations) == 2
    assert all(item.antecedent_state is AntecedentState.UNRESOLVED for item in citations)
    assert all(item.antecedent_id is None for item in citations)


def test_citation_spans_remain_on_the_source_page() -> None:
    raw = "Header\nRoe v. Wade, 410 U.S. 113 (1973)."
    citation = extract_citations(document(raw))[0]

    assert citation.original_span.page == 1
    assert raw[citation.original_span.start:citation.original_span.end] == citation.raw_text
    assert "410 U.S. 113" in citation.raw_text


def test_context_span_is_a_bounded_original_sentence_window() -> None:
    raw = "The first sentence supplies context. Roe v. Wade, 410 U.S. 113 (1973), supports privacy. Later text."
    citation = extract_citations(document(raw))[0]

    assert citation.context_span.page == 1
    context = raw[citation.context_span.start : citation.context_span.end]
    assert context == "The first sentence supplies context. Roe v. Wade, 410 U.S. 113 (1973), supports privacy."


def test_attaches_inline_and_preceding_sentence_quotes_only() -> None:
    inline = extract_citations(document('"The Court held this." Roe v. Wade, 410 U.S. 113 (1973).'))[0]
    preceding = extract_citations(
        document('"The Court held this." This is another sentence. Roe v. Wade, 410 U.S. 113 (1973).')
    )[0]
    unrelated = extract_citations(
        document('"The Court held this." A different point appears here. Roe v. Wade, 410 U.S. 113 (1973).')
    )[0]

    assert inline.quote and inline.quote.text == "The Court held this."
    assert preceding.quote is None
    assert unrelated.quote is None


def test_attaches_a_quote_in_the_same_citing_sentence() -> None:
    citation = extract_citations(
        document('Roe v. Wade, 410 U.S. 113 (1973), held that "the rule applies."')
    )[0]
    assert citation.quote is not None
    assert citation.quote.text == "the rule applies."
    assert citation.quote.processing_text == "the rule applies."


def test_attaches_quote_from_immediately_preceding_sentence() -> None:
    citation = extract_citations(
        document('"The Court held this." Roe v. Wade, 410 U.S. 113 (1973).')
    )[0]
    assert citation.quote is not None
    assert citation.quote.text == "The Court held this."


def test_attaches_visibly_indented_associated_block_quote() -> None:
    raw = '    "The Court held\n    this rule applies"\nRoe v. Wade, 410 U.S. 113 (1973).'
    citation = extract_citations(document(raw))[0]
    assert citation.quote is not None
    assert citation.quote.is_block is True
    assert "The Court held" in citation.quote.text


def test_skips_table_of_authorities_and_its_continuation_pages():
    citations = extract_citations(
        document(
            "ii\nTABLE OF AUTHORITIES\nRoe v. Wade, 410 U.S. 113 (1973) .... 3",
            "iii\nBrown v. Board of Education, 347 U.S. 483 (1954) .... 4",
            "1\nARGUMENT\nRoe v. Wade, 410 U.S. 113 (1973), held that \"the rule applies.\"",
        )
    )

    assert len(citations) == 1
    assert citations[0].raw_text.endswith('Roe v. Wade, 410 U.S. 113 (1973)')
    assert citations[0].original_span.page == 3
