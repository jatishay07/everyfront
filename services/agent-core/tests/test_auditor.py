"""agent_core.agents.auditor -- WO6 (LEDGER): the NCCI PTP/MUE lookups were
never built or passed to `rules.audit.audit_line_items` at all, and the
cash-price lookup depended entirely on a live, per-request MRF fetch even
though a hospital's cash prices can be pre-cached once at seed time. These
tests cover both fixes plus the overlap-safe `total_findings_cents`.
"""

from __future__ import annotations

import asyncio

from _helpers import make_memory_store
from agent_core import mrf_cache, ncci_cache
from agent_core.agents import auditor


def _reset_ncci_cache():
    ncci_cache._table = None
    ncci_cache._attempted = False
    ncci_cache._open_error = None


def _seed_bill_doc(store, case_id, line_items):
    store.add_document(case_id, {"type": "itemized_bill", "extracted": {"line_items": line_items}})


class TestPTPAndMUEWiring:
    def test_ptp_lookup_is_built_and_passed_when_ncci_cache_available(self, monkeypatch):
        """Regression for the original defect: previously `audit_line_items`
        was called with `ptp_lookup=None`/`mue_lookup=None` unconditionally."""
        _reset_ncci_cache()
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        case_id = "case-1"
        # 99213 + 99214 is a real NCCI PTP pair in a hand-built fixture table
        # (see test_ncci_cache.py's use of the bundled snapshot for a case
        # that IS real -- here we just confirm auditor actually calls
        # ncci_cache and gets a real callable back, not None).
        _seed_bill_doc(s, case_id, [{"code": "99213", "units": 1, "charge_cents": 100}])

        captured = {}
        real_audit = auditor.rules_bridge.audit_line_items

        def spy_audit_line_items(items, **kwargs):
            captured.update(kwargs)
            return real_audit(items, **kwargs)

        monkeypatch.setattr(auditor.rules_bridge, "audit_line_items", spy_audit_line_items)

        asyncio.run(auditor._facts(case_id, {"hospital": {}}))
        assert captured["ptp_lookup"] is not None
        assert captured["mue_lookup"] is not None

    def test_ncci_source_reports_bundled_snapshot_when_available(self, monkeypatch):
        _reset_ncci_cache()
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        fact = asyncio.run(auditor._facts("case-1", {"hospital": {}}))
        assert "bundled NCCI snapshot" in fact["ncci_source"]

    def test_ncci_source_is_honest_when_datapipes_unavailable(self, monkeypatch):
        _reset_ncci_cache()
        monkeypatch.setattr(ncci_cache, "load_default", None)
        monkeypatch.setattr(ncci_cache, "_IMPORT_ERROR", "No module named 'datapipes'")
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        fact = asyncio.run(auditor._facts("case-1", {"hospital": {}}))
        assert "skipped" in fact["ncci_source"]
        assert "not importable" in fact["ncci_source"]
        _reset_ncci_cache()


class TestCashPriceLookupPreference:
    def test_prefers_pre_cached_hospital_cash_prices_over_live_fetch(self, monkeypatch):
        """The hospital's own `cash_prices` field (LEDGER's seed-time
        pre-cache) must win over a live mrf_cache fetch -- and the live path
        must not even be invoked for a code the pre-cache already covers."""
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        case_id = "case-1"
        _seed_bill_doc(s, case_id, [{"code": "86787", "units": 1, "charge_cents": 140_00}])

        async def _boom(mrf_url, codes):
            raise AssertionError("live MRF fetch should not run for a pre-cached code")

        monkeypatch.setattr(mrf_cache, "cash_price_lookup_for", _boom)
        hospital = {"mrf_url": "https://x/mrf.csv", "cash_prices": {"86787": 70_00}}
        fact = asyncio.run(auditor._facts(case_id, {"hospital": hospital}))

        cash = [f for f in fact["findings"] if f["kind"] == "cash_price_delta"]
        assert len(cash) == 1
        assert cash[0]["amount_cents"] == 70_00
        assert "pre-cached at seed time" in fact["cash_price_source"]

    def test_falls_back_to_live_fetch_for_codes_missing_from_the_pre_cache(self, monkeypatch):
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        case_id = "case-1"
        _seed_bill_doc(
            s,
            case_id,
            [
                {"code": "86787", "units": 1, "charge_cents": 140_00},
                {"code": "71046", "units": 1, "charge_cents": 320_00},
            ],
        )

        async def _fake_live(mrf_url, codes):
            assert codes == ["71046"]  # only the code missing from the pre-cache
            return lambda code: {"71046": 160_00}.get(code)

        monkeypatch.setattr(mrf_cache, "cash_price_lookup_for", _fake_live)
        hospital = {"mrf_url": "https://x/mrf.csv", "cash_prices": {"86787": 70_00}}
        fact = asyncio.run(auditor._facts(case_id, {"hospital": hospital}))

        cash = {
            f["codes"][0]: f["amount_cents"]
            for f in fact["findings"]
            if f["kind"] == "cash_price_delta"
        }
        assert cash == {"86787": 70_00, "71046": 160_00}
        assert "pre-cached at seed time (1 code(s))" in fact["cash_price_source"]
        assert "live MRF fetch for 1 code(s)" in fact["cash_price_source"]

    def test_no_pre_cache_and_no_mrf_url_is_reported_honestly(self, monkeypatch):
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        case_id = "case-1"
        _seed_bill_doc(s, case_id, [{"code": "80053", "units": 1, "charge_cents": 220_00}])
        fact = asyncio.run(auditor._facts(case_id, {"hospital": {}}))
        assert not any(f["kind"] == "cash_price_delta" for f in fact["findings"])
        assert "no cash price data available" in fact["cash_price_source"]


class TestTotalFindingsCentsIsOverlapSafe:
    def test_duplicate_and_cash_price_delta_on_the_same_lines_do_not_stack(self, monkeypatch):
        """Real shape from case_07_il_concurrent_clocks: two identical 80053
        lines are both an exact duplicate AND, independently, each overpriced
        vs. the hospital's cash price. The two theories must not sum."""
        s = make_memory_store()
        monkeypatch.setattr(auditor, "store", s)
        case_id = "case-1"
        _seed_bill_doc(
            s,
            case_id,
            [
                {"code": "80053", "units": 1, "charge_cents": 220_00},
                {"code": "80053", "units": 1, "charge_cents": 220_00},
            ],
        )
        hospital = {"cash_prices": {"80053": 107_50}}
        fact = asyncio.run(auditor._facts(case_id, {"hospital": hospital}))

        kinds = {f["kind"] for f in fact["findings"]}
        assert "duplicate" in kinds
        assert "cash_price_delta" in kinds
        # naive sum would be 220_00 (duplicate) + 112_50 * 2 (cash delta) = 445_00
        assert fact["total_findings_cents"] == 225_00
