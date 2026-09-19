"""Domain-policy checks for investigator candidate fetches.

The Browserbase search page is discovery-only.  These helpers apply to the
candidate URL that would be navigated to for extraction, and deliberately
fail closed for malformed URLs or schemes other than HTTP(S).
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from urllib.parse import urlsplit


def _host(url: str) -> str | None:
    """Return a normalised HTTP(S) hostname, or ``None`` for unsafe input."""

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return None

    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return hostname.rstrip(".").casefold()


def _matches(hostname: str, pattern: str) -> bool:
    normalised = pattern.strip().rstrip(".").casefold()
    if not normalised:
        return False
    # A configured apex domain includes its normal web host, e.g.
    # ``ecfr.gov`` includes ``www.ecfr.gov``.  Wildcards retain normal glob
    # semantics so ``*.uscourts.gov`` does not silently include the apex.
    return (
        fnmatchcase(hostname, normalised)
        if any(character in normalised for character in "*?[")
        else hostname == normalised or hostname.endswith(f".{normalised}")
    )


def is_denylisted(url: str, denylist: Iterable[str]) -> bool:
    """Whether a candidate URL is prohibited by the configured denylist."""

    hostname = _host(url)
    return hostname is not None and any(_matches(hostname, entry) for entry in denylist)


def is_allowlisted(url: str, allowlist: Iterable[str]) -> bool:
    """Whether a candidate URL is in the configured official-domain allowlist."""

    hostname = _host(url)
    return hostname is not None and any(
        _matches(hostname, entry) for entry in allowlist
    )


def may_fetch(url: str, *, allowlist: Iterable[str], denylist: Iterable[str]) -> bool:
    """Return whether the investigator may fetch ``url``.

    Denylist precedence is intentional and must not be moved to a caller:
    INV-4 requires this check at the fetch boundary itself.
    """

    return not is_denylisted(url, denylist) and is_allowlisted(url, allowlist)
