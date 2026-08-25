"""agent_core.ncci_cache -- wires LEDGER's bundled NCCI PTP/MUE snapshot into
`rules.audit.audit_line_items` (WO6 defect: this was never built/passed at
all -- see agents/auditor.py's docstring for the live transcript that proved
it).

Unlike mrf_cache (per-hospital, network-bound, must be re-mocked per test),
NCCI data is process-global and file-backed, so most tests here reset the
module-level singleton (`_table`, `_attempted`, `_open_error`) rather than
mocking a network call.
"""

from __future__ import annotations

from agent_core import ncci_cache


def _reset():
    ncci_cache._table = None
    ncci_cache._attempted = False
    ncci_cache._open_error = None


def test_bundled_snapshot_is_available_and_answers_a_real_lookup():
    _reset()
    assert ncci_cache.available() is True
    assert ncci_cache.unavailable_reason() is None


def test_mue_lookup_returns_a_working_callable():
    _reset()
    mue = ncci_cache.mue_lookup()
    assert mue is not None
    # 71046 (chest x-ray, 2 views) carries a real MUE ceiling in the bundled
    # table -- confirmed live 2026-08-25 (see docs/ this work order's PR).
    assert mue("71046") is not None
    assert mue("ZZZZZ-not-a-real-code") is None


def test_ptp_lookup_returns_a_working_callable():
    _reset()
    ptp = ncci_cache.ptp_lookup()
    assert ptp is not None
    # No PTP edit exists between two arbitrary made-up codes.
    assert ptp("ZZZZZ", "YYYYY") is None


def test_table_is_opened_once_and_memoized(monkeypatch):
    _reset()
    calls = []
    real_load_default = ncci_cache.load_default

    def _counting_load_default():
        calls.append(1)
        return real_load_default()

    monkeypatch.setattr(ncci_cache, "load_default", _counting_load_default)
    ncci_cache.available()
    ncci_cache.available()
    ncci_cache.ptp_lookup()
    assert len(calls) == 1


def test_unavailable_when_datapipes_not_importable(monkeypatch):
    _reset()
    monkeypatch.setattr(ncci_cache, "load_default", None)
    monkeypatch.setattr(ncci_cache, "_IMPORT_ERROR", "No module named 'datapipes'")
    assert ncci_cache.available() is False
    assert "not importable" in ncci_cache.unavailable_reason()
    assert ncci_cache.ptp_lookup() is None
    assert ncci_cache.mue_lookup() is None


def test_unavailable_when_snapshot_file_is_missing(monkeypatch):
    _reset()

    def _boom():
        raise FileNotFoundError("bundled NCCI snapshot not found")

    monkeypatch.setattr(ncci_cache, "load_default", _boom)
    assert ncci_cache.available() is False
    assert "failed to open" in ncci_cache.unavailable_reason()
    assert ncci_cache.ptp_lookup() is None
