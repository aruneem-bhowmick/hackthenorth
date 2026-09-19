from verdicts import (
    QuoteVerdict,
    SemanticCheckStatus,
    SourceParagraph,
    check_quote,
    normalise,
)


def result(quote: str, *sources: SourceParagraph, pinpoint_page: int | None = None):
    return check_quote(quote, sources, pinpoint_page=pinpoint_page)


def test_exact_quote_is_verbatim():
    checked = result("The Court shall decide the case.", SourceParagraph("one", "The Court shall decide the case."))

    assert checked.verdict is QuoteVerdict.VERBATIM
    assert checked.similarity == 1.0
    assert checked.evidence and checked.evidence.paragraph_id == "one"


def test_bracket_alteration_is_permitted():
    checked = result("[T]he Court shall decide.", SourceParagraph("one", "The Court shall decide."))

    assert checked.verdict is QuoteVerdict.VERBATIM_WITH_PERMITTED_ALTERATIONS
    assert checked.similarity == 1.0
    assert all(change.operation == "equal" for change in checked.diff)


def test_editorial_bracket_note_is_permitted():
    checked = result("The Court shall decide [emphasis added].", SourceParagraph("one", "The Court shall decide."))

    assert checked.verdict is QuoteVerdict.VERBATIM_WITH_PERMITTED_ALTERATIONS


def test_ordered_ellipsis_is_permitted_without_diffing_omitted_text():
    checked = result(
        "The Court shall decide ... after hearing every party.",
        SourceParagraph("one", "The Court shall decide the case carefully after hearing every party."),
    )

    assert checked.verdict is QuoteVerdict.VERBATIM_WITH_PERMITTED_ALTERATIONS
    assert all(change.operation == "equal" for change in checked.diff)


def test_out_of_order_ellipsis_is_not_permitted():
    checked = result(
        "The Court shall decide ... after hearing every party.",
        SourceParagraph("one", "After hearing every party, the Court shall decide."),
    )

    assert checked.verdict is QuoteVerdict.NOT_FOUND_IN_SOURCE
    assert checked.semantic_check_status is SemanticCheckStatus.NOT_CONFIGURED
    assert checked.closest_passage is not None


def test_single_inserted_word_is_altered_with_a_word_diff():
    checked = result(
        "The Court shall unanimously decide the case.",
        SourceParagraph("one", "The Court shall decide the case."),
    )

    assert checked.verdict is QuoteVerdict.ALTERED
    assert any(change.operation in {"delete", "replace"} and "unanimously" in change.quote_tokens for change in checked.diff)


def test_negation_flip_is_at_least_altered():
    checked = result("The Court shall not decide the case.", SourceParagraph("one", "The Court shall decide the case."))

    assert checked.verdict is QuoteVerdict.ALTERED
    assert checked.similarity < 0.97


def test_normalisation_handles_smart_quotes_hyphenation_and_footnotes():
    checked = result(
        '“The interrupted record”',
        SourceParagraph("one", "\"The inter-\nrupted record\" [12]"),
    )

    assert normalise("A  —  B") == "A - B"
    assert checked.verdict is QuoteVerdict.VERBATIM


def test_invented_quote_returns_closest_actual_language_and_semantic_status():
    checked = result(
        "Courts always ignore every statute.",
        SourceParagraph("one", "Courts must apply the statute according to its text."),
    )

    assert checked.verdict is QuoteVerdict.NOT_FOUND_IN_SOURCE
    assert checked.closest_passage is not None
    assert checked.semantic_check_status is SemanticCheckStatus.NOT_CONFIGURED
    assert "SEMANTIC_PROVIDER_REQUIRED" in checked.notes


def test_real_quote_in_dissent_retains_part_evidence_and_pinpoint_note():
    checked = result(
        "The statute does not authorize this result.",
        SourceParagraph("dissent-4", "The statute does not authorize this result.", page=45, opinion_part="dissent"),
        pinpoint_page=44,
    )

    assert checked.verdict is QuoteVerdict.VERBATIM
    assert checked.evidence and checked.evidence.opinion_part == "dissent"
    assert "PINPOINT_MISMATCH" in checked.notes


def test_source_unavailable_has_no_red_verdict():
    checked = result("A quote", SourceParagraph("empty", ""))

    assert checked.verdict is QuoteVerdict.SOURCE_UNAVAILABLE
