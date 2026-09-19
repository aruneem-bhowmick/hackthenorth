from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.reporting import ExportLanguageError, lint_export_text, render_markdown, render_pdf


def _bundle() -> tuple[SimpleNamespace, list[SimpleNamespace], dict[str, SimpleNamespace]]:
    source = SimpleNamespace(
        id="source-1",
        paragraphs=[SimpleNamespace(id="paragraph-1", para_no=1, text="The source uses different language.")],
        provenance=SimpleNamespace(
            url="https://www.courtlistener.com/opinion/1/",
            method="courtlistener",
            retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
            sha256="a" * 64,
        ),
    )
    citation = SimpleNamespace(
        id="citation-1",
        raw_text="Example v. Example, 1 F.4th 2",
        page=3,
        start_offset=10,
        source_acquisition=SimpleNamespace(source_id="source-1", source_url="https://fallback.example/source"),
        claims=[SimpleNamespace(quote_text="Brief text sk-abcdefghijklmnopqrstuvwxyz", proposition_text=None)],
        findings=[
            SimpleNamespace(
                id="finding-1",
                check="quote",
                verdict="ALTERED",
                created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
                evidence={"paragraph_id": "paragraph-1"},
            )
        ],
    )
    return SimpleNamespace(filename="brief.pdf"), [citation], {"source-1": source}


@pytest.mark.parametrize("text", ["fabricated", "fake", "lied", "lying", "AI-generated", "AI generated", "cheating"])
def test_language_linter_rejects_every_prohibited_pattern(text: str) -> None:
    with pytest.raises(ExportLanguageError):
        lint_export_text(f"This export says {text}.")


def test_fixlist_contains_disclaimer_source_language_link_and_provenance() -> None:
    job, citations, sources = _bundle()

    report = render_markdown(job, citations, sources, "fixlist")

    lint_export_text(report)
    assert "It is not legal advice and does not assess anyone's intent." in report
    assert "The quoted language differs from the source material reviewed" in report
    assert "The source uses different language." in report
    assert "https://www.courtlistener.com/opinion/1/" in report
    assert "sha256=" + "a" * 64 in report


def test_evidence_export_redacts_secrets_and_internal_connection_strings() -> None:
    job, citations, sources = _bundle()
    citations[0].claims[0].quote_text = "token sk-abcdefghijklmnopqrstuvwxyz and postgresql://name:pw@localhost/db"

    report = render_markdown(job, citations, sources, "evidence")

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in report
    assert "postgresql://name:pw@localhost/db" not in report
    assert "[redacted secret]" in report
    assert "[redacted connection string]" in report


def test_pdf_export_is_a_pdf_and_is_rendered_from_linted_markdown() -> None:
    job, citations, sources = _bundle()
    report = render_markdown(job, citations, sources, "fixlist")
    lint_export_text(report)

    rendered = render_pdf(report)

    assert rendered.startswith(b"%PDF")
