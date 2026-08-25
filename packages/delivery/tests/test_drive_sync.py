"""Drive mirroring -- §4 persona 4 WO6. Only the graceful-degradation path is
exercised without a live Google API client (see test_calendar_sync.py for
why this needs no `google-api-python-client` install)."""

from __future__ import annotations

from delivery.drive_sync import mirror_case_filings


def test_mirror_case_filings_degrades_gracefully_without_credentials(monkeypatch):
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    filings = [{"filename": "ppdr.pdf", "pdf_bytes": b"%PDF", "front": "ppdr"}]
    # Must return None rather than raise -- a missing Drive mirror must never
    # fail the filing that already produced the PDF.
    assert mirror_case_filings("case_1", filings) is None
