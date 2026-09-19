"""Page-aware PDF text extraction and conservative processing normalisation."""

from __future__ import annotations

import io
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .models import IngestedDocument, NormalisedPage, PageText

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PAGES = 150


class IngestionError(ValueError):
    """A safe-to-display ingestion failure.

    The message deliberately contains no extracted brief text or filesystem
    location, so callers may use it as the API/SSE message without leaking it.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_QUOTE_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
})


def _is_line_number(line: str) -> bool:
    """Only remove a line-number artefact when it is unambiguously one."""

    return bool(re.fullmatch(r"\s*\d{1,3}\s*", line))


def _strip_repeated_running_lines(page_texts: list[str]) -> list[set[int]]:
    """Return first/last line indexes safely identified as running matter.

    We only remove exactly identical nonnumeric lines appearing on at least two
    pages.  This is intentionally conservative: a one-page caption or a mere
    common word must survive processing and its span map.
    """

    candidates: dict[str, list[tuple[int, int]]] = {}
    for page_index, text in enumerate(page_texts):
        lines = text.splitlines()
        nonempty = [(i, line.strip()) for i, line in enumerate(lines) if line.strip()]
        for line_index, value in (nonempty[:1] + nonempty[-1:]):
            if len(value) >= 4 and not _is_line_number(value):
                candidates.setdefault(value.casefold(), []).append((page_index, line_index))

    removed = [set() for _ in page_texts]
    for occurrences in candidates.values():
        if len({page for page, _ in occurrences}) >= 2:
            for page, line in occurrences:
                removed[page].add(line)
    return removed


def _keep_indexes(raw: str, removed_lines: set[int]) -> Iterable[int]:
    line_no = 0
    for index, char in enumerate(raw):
        if line_no not in removed_lines:
            yield index
        if char == "\n":
            line_no += 1


def normalise_page(raw_text: str, *, removed_lines: set[int] | None = None) -> NormalisedPage:
    """Normalise a page while preserving a character-level map to raw text.

    Processing-only changes implement FR-ING-003: repeated headers/footers and
    standalone line numbers may be removed by the caller; line-break
    hyphenation is joined; quote and dash variants are canonicalised; remaining
    whitespace becomes one ASCII space.  Every emitted character retains its
    raw origin, permitting a citation/quote span to be rendered in the PDF.
    """

    removed_lines = removed_lines or set()
    kept = list(_keep_indexes(raw_text, removed_lines))
    out: list[str] = []
    mapping: list[int] = []
    pending_space_origin: int | None = None
    i = 0
    while i < len(kept):
        raw_index = kept[i]
        char = raw_text[raw_index]

        # De-hyphenate only a hyphen followed immediately by a newline and a
        # letter. This avoids touching real in-line hyphens or em dashes.
        if char in "-\u2010\u2011" and i + 2 < len(kept):
            next_index, after_index = kept[i + 1], kept[i + 2]
            if raw_text[next_index] in "\r\n" and raw_text[after_index].isalpha():
                i += 1
                while i < len(kept) and raw_text[kept[i]] in "\r\n":
                    i += 1
                continue

        if char.isspace():
            pending_space_origin = raw_index if pending_space_origin is None else pending_space_origin
            i += 1
            continue

        if pending_space_origin is not None and out:
            out.append(" ")
            mapping.append(pending_space_origin)
        pending_space_origin = None
        out.append(char.translate(_QUOTE_TRANSLATION))
        mapping.append(raw_index)
        i += 1

    # Leading/trailing whitespace is processing noise. Do not manufacture an
    # unmappable character for it.
    return NormalisedPage("".join(out), tuple(mapping))


def extract_pdf(pdf: bytes | bytearray | memoryview | Path | str) -> IngestedDocument:
    """Extract text a page at a time, enforcing the P1 upload boundaries."""

    if isinstance(pdf, (str, Path)):
        payload = Path(pdf).read_bytes()
    else:
        payload = bytes(pdf)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise IngestionError("TOO_LARGE", "PDF uploads are limited to 25 MB.")
    if not payload.startswith(b"%PDF-"):
        raise IngestionError("UNSUPPORTED_FILE", "The upload is not a PDF file.")

    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        count = len(reader.pages)
        if count > MAX_PAGES:
            raise IngestionError("TOO_LARGE", "PDF uploads are limited to 150 pages.")
        raw_pages = [page.extract_text() or "" for page in reader.pages]
    except IngestionError:
        raise
    except (PdfReadError, OSError, ValueError, EOFError) as error:
        raise IngestionError("UNSUPPORTED_FILE", "The PDF could not be read.") from error

    if not any(text.strip() for text in raw_pages):
        raise IngestionError(
            "NO_TEXT_LAYER",
            "This PDF has no extractable text layer. OCR is not available in PinCite.",
        )

    removed_by_page = _strip_repeated_running_lines(raw_pages)
    pages = tuple(
        PageText(
            page=index + 1,
            raw_text=raw,
            normalised=normalise_page(raw, removed_lines=removed_by_page[index]),
        )
        for index, raw in enumerate(raw_pages)
    )
    return IngestedDocument(pages)
