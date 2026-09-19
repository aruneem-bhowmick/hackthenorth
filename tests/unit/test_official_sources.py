from investigator.official_sources import OfficialUrlResolverRule, resolve_official_urls


def test_new_york_slip_opinion_resolver_derives_the_official_reporter_url() -> None:
    rules = (
        OfficialUrlResolverRule(
            name="ny_reporter_3d",
            citation_pattern=r"(?<!\d)(?P<year>\d{4})\s+N\.?(?:Y\.?|Y)\s+Slip\s+Op\s+(?P<number>\d+)(?:\([A-Z]\))?",
            url_template="https://www.nycourts.gov/reporter/3dseries/{year}/{year}_{number}.htm",
        ),
    )

    candidates = resolve_official_urls("2025 NY Slip Op 51938(U)", rules)

    assert [(item.resolver_name, item.url) for item in candidates] == [
        (
            "ny_reporter_3d",
            "https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm",
        )
    ]


def test_unmatched_or_insecure_rules_produce_no_direct_candidate() -> None:
    rules = (
        OfficialUrlResolverRule(
            name="bad",
            citation_pattern=r"(?P<year>\d{4})",
            url_template="http://example.test/{year}",
        ),
    )

    assert resolve_official_urls("no citation", rules) == ()
    assert resolve_official_urls("2025", rules) == ()
