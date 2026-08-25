"""Unit tests for datapipes.select: aggregation + ranking (no network)."""

from __future__ import annotations

from datapipes.schedule_h import Facility, OrgReturn
from datapipes.select import HospitalCandidate, aggregate_org, rank_candidates, select_seed


def _fac(**kw) -> Facility:
    base = dict(
        ein="123456789",
        org_name="ORG",
        state="IL",
        tax_period_end="2023-12-31",
        facility_num="1",
        name="FAC",
        free_care_max_fpl_pct=200,
        discounted_care_max_fpl_pct=300,
        fap_url_raw="http://x.com",
        fap_app_url_raw=None,
        fap_summary_url_raw=None,
        fap_url="http://x.com",
        fap_url_status="usable",
        quirks=[],
    )
    base.update(kw)
    return Facility(**base)


def test_aggregate_picks_usable_over_repaired():
    facilities = [
        _fac(name="A", fap_url="http://repaired.com", fap_url_status="repaired"),
        _fac(name="B", fap_url="http://usable.com", fap_url_status="usable"),
    ]
    org = OrgReturn(
        ein="1", org_name="ORG", state="IL", tax_period_end="2023-12-31", facilities=facilities
    )
    c = aggregate_org(org)
    assert c.fap_url == "http://usable.com"
    assert c.url_status == "usable"


def test_aggregate_flags_varying_thresholds():
    facilities = [
        _fac(name="A", free_care_max_fpl_pct=200),
        _fac(name="B", free_care_max_fpl_pct=400),
    ]
    org = OrgReturn(
        ein="1", org_name="ORG", state="IL", tax_period_end="2023-12-31", facilities=facilities
    )
    c = aggregate_org(org)
    assert c.free_care_max_fpl_pct in (200, 400)  # modal value, either is fine w/ a tie
    assert any("varies across facilities" in q for q in c.quirks)


def test_aggregate_no_usable_url_across_any_facility():
    facilities = [_fac(fap_url=None, fap_url_status="blank")]
    org = OrgReturn(ein="1", org_name="ORG", state="IL", tax_period_end=None, facilities=facilities)
    c = aggregate_org(org)
    assert c.fap_url is None
    assert c.url_status == "blank"


def _candidate(ein, name, state, url_status="usable") -> HospitalCandidate:
    return HospitalCandidate(
        ein=ein,
        name=name,
        state=state,
        tax_period_end="2023-12-31",
        free_care_max_fpl_pct=200,
        discounted_care_max_fpl_pct=300,
        fap_url="http://example.com",
        fap_app_url=None,
        url_status=url_status,
        facility_count=1,
        facility_names=[name],
    )


def test_rank_prioritizes_demo_systems_first():
    candidates = [
        _candidate("1", "Random Community Hospital", "TX"),
        _candidate("2", "Advocate Christ Medical Center", "IL"),
        _candidate("3", "Some California Clinic", "CA"),
    ]
    ranked = rank_candidates(candidates)
    assert ranked[0].name == "Advocate Christ Medical Center"


def test_rank_prioritizes_demo_states_over_other_states():
    candidates = [
        _candidate("1", "Random TX Hospital", "TX"),
        _candidate("2", "Random IL Hospital", "IL"),
    ]
    ranked = rank_candidates(candidates)
    assert ranked[0].state == "IL"


def test_rank_prefers_usable_over_repaired_within_tier():
    candidates = [
        _candidate("1", "IL Hospital Repaired", "IL", url_status="repaired"),
        _candidate("2", "IL Hospital Usable", "IL", url_status="usable"),
    ]
    ranked = rank_candidates(candidates)
    assert ranked[0].name == "IL Hospital Usable"


def test_rank_excludes_candidates_with_no_url():
    candidates = [
        _candidate("1", "No URL Hospital", "IL"),
    ]
    candidates[0].fap_url = None
    ranked = rank_candidates(candidates)
    assert ranked == []


def test_select_seed_without_verify_just_takes_top_n():
    candidates = [_candidate(str(i), f"Hospital {i}", "IL") for i in range(10)]
    chosen = select_seed(candidates, target=3, verify=False)
    assert len(chosen) == 3
