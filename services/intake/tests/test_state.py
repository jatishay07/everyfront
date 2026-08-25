"""Gmail history-id cursor -- fail-open path needs no GCS client installed."""

from __future__ import annotations

from intake import state


def test_get_last_history_id_returns_none_without_a_bucket(monkeypatch):
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    assert state.get_last_history_id() is None


def test_set_last_history_id_is_a_no_op_without_a_bucket(monkeypatch):
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    state.set_last_history_id("12345")  # must not raise
