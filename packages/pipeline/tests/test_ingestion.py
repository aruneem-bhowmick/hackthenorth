from __future__ import annotations

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pipeline.ingestion import IngestionError, extract_pdf, normalise_page


def text_pdf(*pages: str) -> bytes:
    """Create a tiny real text-layer PDF without adding a production dependency."""

    import io

    writer = PdfWriter()
    font = writer._add_object(  # noqa: SLF001 -- pypdf's public writer lacks a text helper
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_normalisation_preserves_offsets_and_processing_rules() -> None:
    raw = "12\nThe court’s well-\nreasoned — opinion  \ncontrols.\n"
    normalised = normalise_page(raw, removed_lines={0})

    assert normalised.text == "The court's wellreasoned - opinion controls."
    start = normalised.text.index("wellreasoned")
    span = normalised.original_span(3, start, start + len("wellreasoned"))
    assert raw[span.start:span.end] == "well-\nreasoned"


@pytest.mark.parametrize(
    "raw",
    [
        "A  B\n\nC",
        "A\tB\r\nC",
        "A\u201cquote\u201d\u2014C",
    ],
)
def test_every_processing_character_has_a_raw_origin(raw: str) -> None:
    page = normalise_page(raw)
    assert len(page.text) == len(page.normalised_to_original)
    assert all(0 <= origin < len(raw) for origin in page.normalised_to_original)


def test_pdf_validation_rejects_non_pdf_without_echoing_upload() -> None:
    with pytest.raises(IngestionError, match="not a PDF") as raised:
        extract_pdf(b"not actually a PDF")
    assert raised.value.code == "UNSUPPORTED_FILE"


def test_pdf_without_text_layer_has_clear_ocr_message() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    payload = bytes()
    import io

    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(IngestionError, match="OCR is not available") as raised:
        extract_pdf(output.getvalue())
    assert raised.value.code == "NO_TEXT_LAYER"


def test_extract_pdf_keeps_text_and_one_based_page_numbers() -> None:
    document = extract_pdf(text_pdf("First page", "Second page"))

    assert document.page_count == 2
    assert [page.page for page in document.pages] == [1, 2]
    assert [page.normalised.text for page in document.pages] == ["First page", "Second page"]
