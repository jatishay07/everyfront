"""text_extract.extract_pdf_text -- WO7's fix for the gap FORGE flagged as
this work order's most important finding: nothing previously extracted text
from a live, Gmail-sourced PDF at all.
"""

from __future__ import annotations

import io

import pytest
from intake import text_extract


def _pdf_with_text(text: str) -> bytes:
    # reportlab is not a services/intake runtime dependency (only pypdf is) --
    # it is the easiest way to fabricate a real, text-bearing PDF for this
    # test, so it's imported lazily and skipped cleanly where absent, same
    # convention as packages/delivery's own optional-dependency tests.
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    del reportlab
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 300))
    c.drawString(10, 150, text)
    c.save()
    return buf.getvalue()


def test_extract_pdf_text_reads_real_text():
    pdf_bytes = _pdf_with_text("SYNTHETIC MEDICAL BILL $2,625.00")
    text = text_extract.extract_pdf_text(pdf_bytes)
    assert "SYNTHETIC MEDICAL BILL" in text
    assert "2,625.00" in text


def test_extract_pdf_text_returns_empty_on_garbage_bytes():
    """A malformed/non-PDF attachment must degrade, not crash intake."""
    assert text_extract.extract_pdf_text(b"not a pdf at all") == ""


def test_extract_pdf_text_returns_empty_on_empty_bytes():
    assert text_extract.extract_pdf_text(b"") == ""


def test_extract_pdf_text_truncates_at_max_chars(monkeypatch):
    monkeypatch.setattr(text_extract, "MAX_CHARS", 10)
    pdf_bytes = _pdf_with_text("0123456789ABCDEFGHIJ")
    text = text_extract.extract_pdf_text(pdf_bytes)
    assert len(text) <= 10
