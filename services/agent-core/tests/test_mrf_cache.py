"""agent_core.mrf_cache -- bounded, cached cash-price lookups for the
cash-price-delta component of `savings_found_cents` (defect #1).

`packages/datapipes` is not on this test suite's sys.path (see
services/agent-core/tests/conftest.py -- only packages/rules is forced onto
it, matching what agent-core's own Cloud Run build context bundles today), so
`mrf_cache._fetch_cash_prices` is None in this process exactly like it would
be in production. Every test here monkeypatches that module attribute
directly rather than relying on a real `datapipes` import, so the caching/
timeout/degradation LOGIC is covered regardless of whether datapipes happens
to be importable in a given environment.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agent_core import mrf_cache


@dataclass
class _FakeCashPrice:
    code: str
    cash: float


def test_unavailable_when_no_datapipes_import(monkeypatch):
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", None)
    result = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert result is None


def test_none_when_no_mrf_url(monkeypatch):
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", lambda url, codes, timeout=4: [])
    result = asyncio.run(mrf_cache.cash_price_lookup_for(None, ["99284"]))
    assert result is None


def test_none_when_no_codes(monkeypatch):
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", lambda url, codes, timeout=4: [])
    result = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", []))
    assert result is None


def test_returns_a_working_lookup_callable(monkeypatch):
    monkeypatch.setattr(mrf_cache, "_cache", {})
    monkeypatch.setattr(
        mrf_cache,
        "_fetch_cash_prices",
        lambda url, codes, timeout=4: [_FakeCashPrice("99284", 70.00)],
    )
    lookup = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert lookup is not None
    assert lookup("99284") == 7000
    assert lookup("00000") is None


def test_no_prices_found_returns_none(monkeypatch):
    monkeypatch.setattr(mrf_cache, "_cache", {})
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", lambda url, codes, timeout=4: [])
    lookup = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert lookup is None


def test_a_slow_fetch_times_out_and_degrades_gracefully(monkeypatch):
    def _slow(url, codes, timeout=4):
        import time

        time.sleep(2)
        return [_FakeCashPrice("99284", 70.00)]

    monkeypatch.setattr(mrf_cache, "_cache", {})
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", _slow)
    monkeypatch.setattr(mrf_cache, "MRF_FETCH_DEADLINE_S", 0.05)
    lookup = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert lookup is None


def test_a_failed_fetch_degrades_gracefully_never_raises(monkeypatch):
    def _boom(url, codes, timeout=4):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(mrf_cache, "_cache", {})
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", _boom)
    lookup = asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert lookup is None


def test_repeat_lookup_for_same_hospital_and_codes_is_cached(monkeypatch):
    calls = []

    def _fetch(url, codes, timeout=4):
        calls.append((url, tuple(codes)))
        return [_FakeCashPrice("99284", 70.00)]

    monkeypatch.setattr(mrf_cache, "_cache", {})
    monkeypatch.setattr(mrf_cache, "_fetch_cash_prices", _fetch)

    asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    asyncio.run(mrf_cache.cash_price_lookup_for("https://x/mrf.csv", ["99284"]))
    assert len(calls) == 1  # second call served from the in-process cache
