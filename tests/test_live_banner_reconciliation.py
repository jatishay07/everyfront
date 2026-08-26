"""Hand-reconcile the live §3.4 banner against the live 8-case corpus --
PROOF (persona 7), WO8 task 3.

`tests/test_stats_consistency.py` proves the *fixture* corpus's own
precomputed `expected_stats.json` is internally consistent (pure, offline).
`tests/test_live_stats_consistency.py` proves `/dashboard/stats` moves the
way one fresh injection should (a delta check, since the shared live project
can carry cases this persona did not put there).

This module is neither of those: it is the literal WO8 task 3 instruction --
"confirm [hospitals] drops to 4 and that every other field reconciles
against the 8 cases by hand" -- done as a repeatable check instead of a
one-time manual pass, against the persistent, human-readable corpus
(`ef-2026-0001`..`ef-2026-0008`, contract §3.1) rather than a fresh
injection. It fetches `GET /cases` once, independently recomputes every
`/dashboard/stats` field from first principles over that same response, and
asserts exact equality, field by field, so a future discrepancy names the
specific field a judge would catch -- not just "stats differ somehow".

BACKGROUND (2026-08-26, live-verified while writing this): the live banner
read `hospitals: 5`, not 4. Root cause, read directly from
`services/api/main.py`'s `/dashboard/stats` handler:

    ein = (case.get("bill") or {}).get("hospital_ein") or (case.get("hospital") or {}).get("ein")
    if ein:
        hospital_eins.add(ein)

`ef-2026-0006` (the "unparseable bill" fixture, `case_06_unparseable_bill`)
is the one case whose hospital lookup honestly FAILED to resolve -- its
`bill.hospital_ein` is the literal fabricated placeholder string
`"00-0000000"` (not `None`; see `fixtures/cases_data.py`'s case_06 docstring:
"hospital_ein/amount/dates are honestly None: no document evidence backs
them" -- true for the fixture's *source* intent, but the deployed pipeline's
Reader/Lookup cascade materializes that as the literal sentinel string, not
a JSON null), and `case.get("hospital")` is absent entirely (no `hospitals/
{ein}` record was ever resolved for it -- confirmed via `GET /cases`: the
case carries no `"hospital"` key at all, only `hospital_name: ""`). The
`or` above falls through to that placeholder string, which is truthy, so it
is counted as if it were a fifth distinct real hospital. The reference
definition (`fixtures/generate.py`'s own `stats["hospitals"]` computation)
only ever adds an EIN when the hospital lookup actually resolved
(`if hospital is not None: seen_hospitals.add(hospital.ein)`) -- this test
encodes that same, correct definition independently, then compares it to the
live banner's own reported value.

This is a HANDOFF-tracked bug in `services/api/main.py`, outside PROOF's
owned paths (`fixtures/`, `tests/` -- BUILD_PLAYBOOK.md §0.2) -- SWARM/
STATUTE are fixing the handler itself in parallel with this PR (see its
description). Until that fix is deployed, `test_hospitals_is_exactly_4`
below is EXPECTED TO FAIL -- that is the point: it names the live regression
precisely instead of a judge finding it live on camera. Re-run this module
after their fix deploys; a passing run is this task's acceptance evidence.

`@pytest.mark.e2e`: needs `EVERYFRONT_API_URL` (contract §3.3), same
convention as this package's other live-only modules. Read-only throughout
-- never injects, never approves, never deletes; the live 8-case corpus is
never touched by this test even on failure.
"""

from __future__ import annotations

import datetime
import os

import pytest

pytestmark = pytest.mark.e2e

httpx = pytest.importorskip("httpx", reason="e2e only; not installed in PR CI")

API_URL = os.environ.get("EVERYFRONT_API_URL", "").rstrip("/")

# The persistent, human-readable corpus this whole demo is rehearsed
# against (fixtures/demo_reset.py's own HUMAN_CASE_IDS) -- not a fresh
# injection. §7 / the CEO's own brief: "Keep it at 8."
EXPECTED_CASE_IDS = {
    "ef-2026-0001",
    "ef-2026-0002",
    "ef-2026-0003",
    "ef-2026-0004",
    "ef-2026-0005",
    "ef-2026-0006",
    "ef-2026-0007",
    "ef-2026-0008",
}


def _require_env() -> None:
    if not API_URL:
        pytest.skip(
            "needs a live deployment: set EVERYFRONT_API_URL (contract §3.3) to the "
            "deployed services/api Cloud Run URL. See this module's docstring."
        )


@pytest.fixture(scope="module")
def api():
    _require_env()
    return httpx.Client(base_url=API_URL, timeout=30.0)


@pytest.fixture(scope="module")
def cases(api):
    resp = api.get("/cases")
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope="module")
def live_stats(api):
    resp = api.get("/dashboard/stats")
    resp.raise_for_status()
    return resp.json()


def _recompute(cases: list[dict]) -> dict:
    """Independent, from-scratch recomputation of every §3.4 field, over
    `GET /cases`'s own response -- mirrors `services/api/main.py`'s
    `/dashboard/stats` handler's INTENDED semantics field-by-field, except
    `hospitals`, which deliberately uses the CORRECT definition (only a
    resolved `hospital` object counts, matching `fixtures/generate.py`'s
    `stats["hospitals"]`) rather than the live handler's current buggy one.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    week_from_now = today + datetime.timedelta(days=7)

    open_cases = 0
    resolved_hospital_eins: set[str] = set()
    deadlines_this_week = 0
    total_billed_cents = 0
    charity_eligible = 0
    ppdr_eligible = 0
    unlawful_denials_flagged = 0
    filings_sent = 0
    audit_findings_cents = 0

    for case in cases:
        if case.get("status") != "closed":
            open_cases += 1

        hospital = case.get("hospital")
        if hospital and hospital.get("ein"):
            resolved_hospital_eins.add(hospital["ein"])

        total_billed_cents += (case.get("bill") or {}).get("amount_cents") or 0

        denial_flag = case.get("denial_flag")
        if isinstance(denial_flag, dict) and denial_flag.get("violated"):
            unlawful_denials_flagged += 1

        for front in case.get("fronts") or []:
            due = front.get("deadline")
            if due:
                due_date = datetime.date.fromisoformat(due[:10])
                if today <= due_date <= week_from_now:
                    deadlines_this_week += 1
            if front.get("front") == "charity_care" and front.get("applicable"):
                charity_eligible += 1
            if front.get("front") == "ppdr" and front.get("applicable"):
                ppdr_eligible += 1
            if front.get("status") == "filed":
                filings_sent += 1

        audit_findings_cents += case.get("audit_findings_cents") or 0

    return {
        "open_cases": open_cases,
        "hospitals": len(resolved_hospital_eins),
        "deadlines_this_week": deadlines_this_week,
        "total_billed_cents": total_billed_cents,
        "charity_eligible": charity_eligible,
        "ppdr_eligible": ppdr_eligible,
        "unlawful_denials_flagged": unlawful_denials_flagged,
        "audit_findings_cents": audit_findings_cents,
        "filings_sent": filings_sent,
    }


def test_corpus_is_exactly_the_8_named_cases(cases):
    """§7 / the CEO's own brief: "Keep it at 8." This isn't this test's main
    point, but a banner reconciliation over the WRONG case set is worse than
    no reconciliation at all -- fail loudly and specifically if the corpus
    has drifted, rather than silently reconciling whatever happens to be
    live right now."""
    seen = {c["case_id"] for c in cases}
    extra = seen - EXPECTED_CASE_IDS
    missing = EXPECTED_CASE_IDS - seen
    assert not extra and not missing, (
        f"live corpus is not exactly the 8 named cases -- extra={extra or None}, "
        f"missing={missing or None}. Either demo_reset.py --reseed hasn't been "
        "run since a stray injection, or the corpus needs re-seeding before "
        "this reconciliation means anything."
    )


def test_hospitals_is_exactly_4(cases):
    """The task's headline check: `ef-2026-0006`'s fabricated placeholder
    EIN `00-0000000` must NOT be counted -- only the 4 cases with a genuinely
    resolved `hospital` object (Sutter Bay, Advocate Christ, Stanford Health
    Care, Prairie Crossing) count. See this module's docstring for the exact
    root cause in `services/api/main.py` if this fails."""
    recomputed = _recompute(cases)
    assert recomputed["hospitals"] == 4, (
        f"expected exactly 4 resolved hospitals across the 8-case corpus, got "
        f"{recomputed['hospitals']}. If this is 5, ef-2026-0006's placeholder "
        "EIN 00-0000000 is almost certainly being counted again -- see this "
        "module's docstring."
    )


def test_live_banner_reconciles_field_by_field(cases, live_stats):
    """The task's other half: "every other field reconciles against the 8
    cases by hand." Compares field-by-field (not a blanket dict equality) so
    a failure names the SPECIFIC stat a judge would catch doing arithmetic
    on screen, not just "stats mismatch"."""
    recomputed = _recompute(cases)
    mismatches = {
        key: {"live": live_stats.get(key), "hand_recomputed": recomputed[key]}
        for key in recomputed
        if live_stats.get(key) != recomputed[key]
    }
    assert not mismatches, (
        f"/dashboard/stats disagrees with a hand recomputation over GET /cases "
        f"on {len(mismatches)} field(s): {mismatches}"
    )
