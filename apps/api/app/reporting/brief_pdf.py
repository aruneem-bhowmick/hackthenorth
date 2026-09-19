"""Pure-Python PDF rendering of the annotated brief (FR-RPT).

Verdict-to-tone colors mirror ``verdictDetails`` in
apps/web/src/app/page.tsx; keep the two in sync.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

_TONE_RGB: dict[str, tuple[int, int, int]] = {
    "green": (22, 101, 52),
    "yellow": (146, 64, 14),
    "red": (180, 35, 24),
    "grey": (71, 85, 105),
}
_INK_RGB = (36, 26, 20)

_POSITIVE_VERDICTS = {
    "VERIFIED", "VERIFIED_OFFICIAL", "VERBATIM",
    "VERBATIM_WITH_PERMITTED_ALTERATIONS", "SUPPORTS",
}
_NEGATIVE_VERDICTS = {"NOT_FOUND_ANYWHERE", "NOT_FOUND_IN_SOURCE", "CONTRADICTS"}
_NEUTRAL_VERDICTS = {"NOT_IN_DATABASE", "UNRECOGNIZED", "PENDING", "SOURCE_UNAVAILABLE", "UNVERIFIABLE", ""}


def _tone_for_verdict(verdict: str) -> str:
    if verdict in _POSITIVE_VERDICTS:
        return "green"
    if verdict in _NEGATIVE_VERDICTS:
        return "red"
    if verdict in _NEUTRAL_VERDICTS:
        return "grey"
    return "yellow"


def _worst_tone(verdicts: Sequence[str]) -> str:
    rank = {"green": 1, "grey": 2, "yellow": 3, "red": 4}
    tones = [_tone_for_verdict(verdict) for verdict in verdicts] or ["grey"]
    return max(tones, key=lambda tone: rank[tone])


def _span_tones(citations: Sequence[Any]) -> dict[int, list[tuple[int, int, str]]]:
    """Map each page to its non-overlapping, verdict-colored citation spans."""

    by_page: dict[int, list[tuple[int, int, str]]] = {}
    for citation in citations:
        page = getattr(citation, "page", None)
        start = getattr(citation, "start_offset", None)
        end = getattr(citation, "end_offset", None)
        if page is None or start is None or end is None or end <= start:
            continue
        tone = _worst_tone([finding.verdict for finding in getattr(citation, "findings", [])])
        by_page.setdefault(page, []).append((start, end, tone))
    for spans in by_page.values():
        spans.sort(key=lambda item: (item[0], -item[1]))
    return by_page


def render_annotated_brief_pdf(job: Any, pages: Sequence[Any], citations: Sequence[Any]) -> bytes:
    """Render the extracted brief with verdict-colored, underlined citation spans.

    Colors alone never carry the only signal: annotated spans are also
    underlined so the PDF stays legible without color (e.g. printed in
    black and white).
    """

    spans_by_page = _span_tones(citations)
    pdf = FPDF(unit="pt", format="letter")
    # fpdf2's own internal encode step defaults to strict latin-1 regardless
    # of _latin1()'s pre-sanitization below; align it with the Helvetica core
    # font's actual WinAnsiEncoding so cp1252-safe glyphs (em dash, curly
    # quotes) render instead of raising FPDFUnicodeEncodingException.
    pdf.core_fonts_encoding = "cp1252"
    pdf.set_auto_page_break(auto=True, margin=42)
    pdf.add_page()

    pdf.set_font("Helvetica", style="B", size=16)
    pdf.set_text_color(*_INK_RGB)
    pdf.multi_cell(0, 20, _latin1(f"Annotated brief — {getattr(job, 'filename', 'brief.pdf')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", size=8)
    legend = "Highlight key: green = located/supports, amber = review recommended, red = needs attention, grey = pending or unavailable."
    pdf.multi_cell(0, 11, _latin1(legend), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    for page in sorted(pages, key=lambda item: item.page):
        pdf.set_font("Helvetica", style="B", size=11)
        pdf.set_text_color(*_INK_RGB)
        pdf.multi_cell(0, 15, _latin1(f"Page {page.page}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=9)
        _write_annotated_page(pdf, page.text, spans_by_page.get(page.page, []))
        pdf.ln(10)

    return bytes(pdf.output())


def _write_annotated_page(pdf: FPDF, text: str, spans: list[tuple[int, int, str]]) -> None:
    cursor = 0
    for start, end, tone in spans:
        start = max(cursor, min(start, len(text)))
        end = max(start, min(end, len(text)))
        if start > cursor:
            _write_segment(pdf, text[cursor:start], plain=True)
        if end > start:
            _write_segment(pdf, text[start:end], plain=False, tone=tone)
        cursor = end
    if cursor < len(text):
        _write_segment(pdf, text[cursor:], plain=True)
    pdf.ln(12)


def _write_segment(pdf: FPDF, segment: str, *, plain: bool, tone: str | None = None) -> None:
    if not segment:
        return
    if plain:
        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(*_INK_RGB)
    else:
        pdf.set_font("Helvetica", style="U", size=9)
        pdf.set_text_color(*_TONE_RGB[tone or "grey"])
    pdf.write(13, _latin1(segment))


def _latin1(value: str) -> str:
    """Encode for the Helvetica core font's declared WinAnsiEncoding (cp1252).

    True ISO-8859-1 has no printable characters in 0x80-0x9F, so an em dash,
    curly quote, or other typographic punctuation common in real briefs would
    silently become "?" under plain latin-1. cp1252 (WinAnsi) covers those.
    Unsupported glyphs still fall back to "?" rather than failing the export.
    """

    return value.encode("cp1252", errors="replace").decode("cp1252")
