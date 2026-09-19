"""Deterministic PDF ingestion and legal-citation extraction for Pincite.

The package intentionally does not make network or model calls.  It provides the
page/span-safe hand-off to the worker's resolution tasks required by P1.
"""

from .extraction import extract_citations
from .ingestion import extract_pdf, normalise_page
from .models import Citation, IngestedDocument, PageText, QuoteClaim, TextSpan

__all__ = [
    "Citation",
    "IngestedDocument",
    "PageText",
    "QuoteClaim",
    "TextSpan",
    "extract_citations",
    "extract_pdf",
    "normalise_page",
]
