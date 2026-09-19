"""Neutral-language and accidental-secret guard for generated reports."""

from __future__ import annotations

import re


class ExportLanguageError(ValueError):
    """Raised when report text violates the neutral-language export contract."""


# Keep this intentionally small and reviewable.  These are prohibited
# characterisations of intent or AI use, not ordinary legal terminology.
_DENYLIST = (
    re.compile(r"\bfabricat\w*\b", re.IGNORECASE),
    re.compile(r"\bfake\b", re.IGNORECASE),
    re.compile(r"\b(?:lied|lying|liar)\b", re.IGNORECASE),
    re.compile(r"\bAI[ -]?generated\b", re.IGNORECASE),
    re.compile(r"\b(?:cheated|cheating|cheater)\b", re.IGNORECASE),
)


def lint_export_text(text: str) -> None:
    """Enforce INV-3 before an export is returned to the requester."""

    matches = sorted({match.group(0) for pattern in _DENYLIST for match in pattern.finditer(text)})
    if matches:
        raise ExportLanguageError("export contains prohibited language: " + ", ".join(matches))
