"""Pure-Python PDF rendering for the exact Markdown export content."""

from __future__ import annotations

import re

from fpdf import FPDF
from fpdf.enums import XPos, YPos


def render_pdf(markdown: str) -> bytes:
    """Produce a portable PDF without browser or native-library dependencies."""

    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(auto=True, margin=42)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    for line in markdown.splitlines():
        if line.startswith("# "):
            pdf.set_font("Helvetica", style="B", size=16)
            pdf.multi_cell(0, 20, _pdf_line(line[2:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", size=9)
        elif line.startswith("## ") or line.startswith("### "):
            pdf.set_font("Helvetica", style="B", size=11)
            pdf.multi_cell(0, 15, _pdf_line(line.lstrip("# ")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", size=9)
        else:
            pdf.multi_cell(0, 12, _pdf_line(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def _latin1(value: str) -> str:
    """Core PDF fonts are portable; replace unsupported glyphs rather than failing."""

    return value.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_line(value: str) -> str:
    """Give core-font layout break points for hashes, URLs, and other long tokens."""

    broken = re.sub(r"\S{72,}", lambda match: " ".join(match.group(0)[index : index + 72] for index in range(0, len(match.group(0)), 72)), value)
    return _latin1(broken)
