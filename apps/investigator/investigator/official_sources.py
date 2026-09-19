"""Pure, configured official-citation URL resolution.

Rules convert a recognised public citation format into a candidate URL, but
never establish that it is authoritative. The investigator still applies its
allowlist, browser extraction, and corroborated case match before acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import quote, urlsplit


@dataclass(frozen=True, slots=True)
class OfficialUrlResolverRule:
    name: str
    citation_pattern: str
    url_template: str


@dataclass(frozen=True, slots=True)
class ResolvedOfficialUrl:
    url: str
    resolver_name: str


def resolve_official_urls(
    citation_text: str, rules: tuple[OfficialUrlResolverRule, ...]
) -> tuple[ResolvedOfficialUrl, ...]:
    """Derive safe, de-duplicated candidates from explicitly configured rules."""

    candidates: list[ResolvedOfficialUrl] = []
    seen: set[str] = set()
    for rule in rules:
        match = re.search(rule.citation_pattern, citation_text, flags=re.IGNORECASE)
        if match is None:
            continue
        values = {
            name: quote(value, safe="") for name, value in match.groupdict().items()
        }
        try:
            url = rule.url_template.format(**values)
        except (KeyError, ValueError):
            continue
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or url in seen
        ):
            continue
        seen.add(url)
        candidates.append(ResolvedOfficialUrl(url=url, resolver_name=rule.name))
    return tuple(candidates)


__all__ = ["OfficialUrlResolverRule", "ResolvedOfficialUrl", "resolve_official_urls"]
