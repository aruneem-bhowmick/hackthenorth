from verdicts.existence import (
    CitationContext,
    ExtractedCandidate,
    case_name_similarity,
    load_investigator_thresholds,
    match_candidate,
)


def test_case_name_similarity_ignores_case_and_punctuation() -> None:
    assert case_name_similarity("Smith v. Jones, Inc.", "SMITH V JONES INC") == 1.0


def test_case_name_similarity_keeps_different_cases_distinct() -> None:
    assert case_name_similarity("Smith v. Jones", "State v. Johnson") < 0.8


def test_match_candidate_requires_name_and_one_corroborating_fact() -> None:
    citation = CitationContext(
        case_name="Smith v. Jones", court="Ninth Circuit", year=2024, docket_no="23-100"
    )
    name_only = ExtractedCandidate(case_name="Smith v. Jones")
    assert not match_candidate(name_only, citation, name_similarity_threshold=0.8)
    assert match_candidate(
        ExtractedCandidate(case_name="Smith v. Jones", court="Ninth Circuit"),
        citation,
        name_similarity_threshold=0.8,
    )
    assert match_candidate(
        ExtractedCandidate(case_name="Smith v. Jones", decision_date="2024-01-02"),
        citation,
        name_similarity_threshold=0.8,
    )
    assert match_candidate(
        ExtractedCandidate(case_name="Smith v. Jones", docket_no="23 100"),
        citation,
        name_similarity_threshold=0.8,
    )


def test_match_candidate_rejects_bad_name_even_when_year_matches() -> None:
    citation = CitationContext(case_name="Smith v. Jones", year=2024)
    candidate = ExtractedCandidate(
        case_name="State v. Johnson", decision_date="2024-01-02"
    )
    assert not match_candidate(candidate, citation, name_similarity_threshold=0.8)


def test_load_investigator_thresholds_uses_approved_p3_defaults() -> None:
    thresholds = load_investigator_thresholds()
    assert thresholds.name_similarity_threshold == 0.8
    assert (
        thresholds.max_searches,
        thresholds.max_fetches,
        thresholds.max_wall_time_seconds,
    ) == (3, 5, 90.0)
    assert any(
        rule.domain == "www.nycourts.gov" and rule.citation_marker == "ny slip op"
        for rule in thresholds.official_search_domains
    )
