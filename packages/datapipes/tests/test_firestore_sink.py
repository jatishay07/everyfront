"""Unit tests for datapipes.firestore_sink (no network -- LocalJsonlSink only)."""

from __future__ import annotations

import json

from datapipes.firestore_sink import LocalJsonlSink, get_sink, to_contract_record
from datapipes.select import HospitalCandidate


def _candidate(**kw) -> HospitalCandidate:
    base = dict(
        ein="362169147",
        name="ADVOCATE HEALTH AND HOSPITALS CORP",
        state="IL",
        tax_period_end="2023-12-31",
        free_care_max_fpl_pct=250,
        discounted_care_max_fpl_pct=600,
        fap_url="http://www.advocatehealth.com/fap",
        fap_app_url="http://www.advocatehealth.com/fap",
        url_status="usable",
        facility_count=6,
        facility_names=["A", "B"],
        quirks=[],
    )
    base.update(kw)
    return HospitalCandidate(**base)


def test_contract_record_shape_matches_3_1():
    c = _candidate()
    record = to_contract_record(c, ccn="140291", nonprofit=True, mrf_url="http://mrf.example")
    expected_keys = {
        "name",
        "ccn",
        "state",
        "fap_url",
        "fap_app_url",
        "free_care_max_fpl_pct",
        "discounted_care_max_fpl_pct",
        "source",
        "tax_year",
        "mrf_url",
    }
    assert expected_keys.issubset(record.keys())
    assert record["source"] == "schedule_h"
    assert record["tax_year"] == 2023  # date -> filing year, not the raw ISO date
    assert record["ccn"] == "140291"


def test_for_profit_forces_fap_url_none():
    c = _candidate()
    record = to_contract_record(c, ccn="000001", nonprofit=False, mrf_url=None)
    assert record["fap_url"] is None
    assert record["fap_app_url"] is None
    assert record["nonprofit"] is False


def test_unknown_nonprofit_keeps_published_fap_url():
    c = _candidate()
    record = to_contract_record(c, ccn=None, nonprofit=None, mrf_url=None)
    assert record["fap_url"] == c.fap_url
    assert record["nonprofit"] is None


def test_local_jsonl_sink_roundtrip(tmp_path):
    path = tmp_path / "seed.jsonl"
    sink = LocalJsonlSink(path)
    sink.write("111111111", {"name": "A"})
    sink.write("222222222", {"name": "B"})
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    rows = {json.loads(line)["_ein"]: json.loads(line) for line in lines}
    assert rows["111111111"]["name"] == "A"


def test_local_jsonl_sink_overwrites_same_ein(tmp_path):
    path = tmp_path / "seed.jsonl"
    sink = LocalJsonlSink(path)
    sink.write("111111111", {"name": "OLD"})
    sink.write("111111111", {"name": "NEW"})
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "NEW"


def test_get_sink_dry_run_returns_local(tmp_path):
    sink = get_sink(dry_run=True, local_path=tmp_path / "x.jsonl")
    assert isinstance(sink, LocalJsonlSink)
