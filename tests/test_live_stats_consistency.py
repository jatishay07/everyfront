"""Live §3.4 stats-consistency check -- PROOF (persona 7), WO5.

`tests/test_stats_consistency.py` proves the §3.4 stat object PROOF's own
`fixtures/generate.py` computes agrees with the corpus that produced it --
pure, offline, no live dependency. This module is the other half of WO5's
mandate ("if the live API's `/dashboard/stats` disagrees with your
fixtures, that is a real bug"): it injects one known fixture against a
*live* deployment and checks that `GET /dashboard/stats` moves by exactly
the amount that one case should contribute, not by asserting the whole
banner equals the corpus in isolation -- the live project is shared with
every other persona's own manual/automated testing (verified directly:
`open_cases` climbed from single digits into the 30s over the course of
this PR's live-verification pass), so an absolute-equality assertion would
be a false negative from day one. A delta check is the strongest claim that
is actually true in a shared environment.

Needs only `EVERYFRONT_API_URL` (contract §3.3) -- no GCP credentials, no
Firestore client, unlike tests/test_e2e_happy_path.py. That test's own
`EVERYFRONT_STAGING_API_URL` + `GOOGLE_CLOUD_PROJECT` requirement is why it
could not be exercised directly in this sandbox; this module deliberately
needs less so it can run wherever `fixtures/demo_run.py` can.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e

httpx = pytest.importorskip("httpx", reason="e2e only; not installed in PR CI")

API_URL = os.environ.get("EVERYFRONT_API_URL", "").rstrip("/")
FIXTURE_NAME = "case_01_uninsured_gfe_ca"
INJECT_TIMEOUT_S = float(os.environ.get("EVERYFRONT_INJECT_TIMEOUT_S", "360"))


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


def test_open_cases_increases_by_exactly_one_case_injected(api):
    """The one stat every injection unambiguously moves by exactly 1,
    regardless of whatever else is already in the shared project."""
    before = api.get("/dashboard/stats")
    before.raise_for_status()
    open_before = before.json()["open_cases"]

    resp = api.post(
        "/demo/inject_bill", json={"fixture_name": FIXTURE_NAME}, timeout=INJECT_TIMEOUT_S
    )
    resp.raise_for_status()

    after = api.get("/dashboard/stats")
    after.raise_for_status()
    open_after = after.json()["open_cases"]

    assert open_after == open_before + 1, (
        f"injecting one case moved open_cases by {open_after - open_before}, not 1 -- "
        "either the injection didn't create a case, or something else concurrently "
        "changed the case count mid-test"
    )


def test_dashboard_stats_has_every_contract_3_4_key(api):
    resp = api.get("/dashboard/stats")
    resp.raise_for_status()
    stats = resp.json()
    expected_keys = {
        "open_cases",
        "hospitals",
        "deadlines_this_week",
        "total_billed_cents",
        "charity_eligible",
        "ppdr_eligible",
        "unlawful_denials_flagged",
        "audit_findings_cents",
        "filings_sent",
        # AMENDED 2026-08-26 (FORGE, on PROOF's behalf -- flagged by the agent
        # that added it). Every filing so far is a recording stub, and the
        # banner said only "filings sent". This assertion is `==`, not `<=`,
        # deliberately: an UNEXPECTED key is drift worth failing on. That
        # strictness is why it had to be updated here in the same change that
        # added the key, rather than discovered live during a rehearsal.
        "filings_simulated",
        "human_hours",
    }
    assert set(stats) == expected_keys


def test_dashboard_stats_are_never_negative(api):
    """A judge doing arithmetic on screen must not catch a discrepancy --
    a negative count/total is the most obvious possible one."""
    resp = api.get("/dashboard/stats")
    resp.raise_for_status()
    stats = resp.json()
    for key, value in stats.items():
        assert value >= 0, f"{key}={value} is negative"
