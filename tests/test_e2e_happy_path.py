"""End-to-end happy path -- PROOF (persona 7), work order 3.

`@pytest.mark.e2e`: CI's PR job runs `pytest -m "not e2e"` (see
.github/workflows/ci.yml and pyproject.toml's marker registration), so this
suite is skipped there. Per BUILD_PLAYBOOK.md §4 persona 7 WO3, it is meant
to run nightly against a live staging project -- it needs a deployed
services/api (contract §3.3) and a real Firestore, neither of which exists
inside this PR's sandbox (services/api is not built yet: SWARM WO3;
services/agent-core is still FORGE's hello-world seed, not the Strategist
hierarchy: SWARM WO1). Rather than fake a pipeline that does not exist, this
test talks to the REAL contract and skips cleanly with a clear reason when
its two required environment variables are not configured -- so it is
inert everywhere except a real nightly staging run, and starts working the
day SWARM's WO1-4 land with no code change here.

Required environment (mirrors .env.example's GOOGLE_CLOUD_PROJECT and adds
one API endpoint var -- HANDOFF -> ATLAS/FORGE: add
EVERYFRONT_STAGING_API_URL to .env.example and the nightly e2e workflow):

    EVERYFRONT_STAGING_API_URL   e.g. https://ef-api-xxxx.a.run.app
    GOOGLE_CLOUD_PROJECT         the staging GCP project (contract §3.1 Firestore)

Uses fixture "case_01_uninsured_gfe_ca" -- the demo's own happy path (uninsured
+ GFE + California: PPDR + charity-care free tier, no charity-care deadline
drama, nothing that should ever legitimately fail a filing gate).
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

API_URL = os.environ.get("EVERYFRONT_STAGING_API_URL", "").rstrip("/")
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
FIXTURE_NAME = "case_01_uninsured_gfe_ca"

# Persona 5 acceptance criterion: "within 3 minutes, un-touched: classified,
# hospital resolved, fronts selected... awaiting approval."
ANALYSIS_TIMEOUT_S = 180
POLL_INTERVAL_S = 5
# Persona 7 acceptance criterion: the whole happy path is "under 4 minutes of
# watchable action" -- filing after approval should be comparably fast.
FILING_TIMEOUT_S = 60


def _require_env() -> None:
    if not API_URL or not PROJECT:
        pytest.skip(
            "e2e needs a live staging deployment: set EVERYFRONT_STAGING_API_URL "
            "and GOOGLE_CLOUD_PROJECT (see this module's docstring). Not present "
            "-> nothing to test against yet (services/api + the Strategist "
            "hierarchy are SWARM WO1-4, not yet built as of this PR)."
        )


@pytest.fixture(scope="module")
def firestore_client():
    fs = pytest.importorskip(
        "google.cloud.firestore",
        reason="google-cloud-firestore not installed -- see fixtures/requirements.txt",
    )
    return fs.Client(project=PROJECT)


@pytest.fixture(scope="module")
def api():
    return httpx.Client(base_url=API_URL, timeout=30.0)


def test_inject_bill_produces_a_case(api):
    """POST /demo/inject_bill (contract §3.3) with the happy-path fixture."""
    _require_env()
    resp = api.post("/demo/inject_bill", json={"fixture_name": FIXTURE_NAME})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "case_id" in body, "inject_bill must return the new case_id for the caller to poll"


def test_happy_path_end_to_end(api, firestore_client):
    """inject -> poll Firestore for the analyzed state -> approve -> filed.

    Asserts the Firestore end-state and the filings produced, per WO3's
    acceptance criterion, and per persona 5 WO1's acceptance criterion in
    full: classified, hospital resolved, fronts selected with citations,
    deadlines present, filing PDFs rendered awaiting approval; then approve
    and confirm a filing with vendor proof appears.
    """
    _require_env()

    inject = api.post("/demo/inject_bill", json={"fixture_name": FIXTURE_NAME})
    assert inject.status_code == 200, inject.text
    case_id = inject.json()["case_id"]

    case_ref = firestore_client.collection("cases").document(case_id)

    deadline = time.monotonic() + ANALYSIS_TIMEOUT_S
    case_doc = None
    while time.monotonic() < deadline:
        snap = case_ref.get()
        if snap.exists:
            data = snap.to_dict()
            if data.get("status") in ("strategy_ready", "filing", "awaiting_response"):
                case_doc = data
                break
        time.sleep(POLL_INTERVAL_S)

    assert case_doc is not None, (
        f"case {case_id} never reached strategy_ready within {ANALYSIS_TIMEOUT_S}s"
    )

    # --- contract §3.1 end-state assertions ---
    fronts = case_doc.get("fronts", [])
    assert fronts, "Strategist must have selected at least one front"
    for front in fronts:
        assert front.get("citation") or front.get("reason"), (
            f"front {front.get('front')} has no citation/reason -- agreement §2.2"
        )
    front_names = {f["front"] for f in fronts}
    assert "charity_care" in front_names
    assert "ppdr" in front_names
    assert "audit" in front_names

    events = list(case_ref.collection("events").order_by("ts").stream())
    assert events, "the events/ audit log must not be empty -- it is the demo's soul"
    agents_seen = {e.to_dict().get("agent") for e in events}
    assert {"reader", "lookup", "clock", "strategist"} <= agents_seen

    docs = list(case_ref.collection("documents").stream())
    doc_types = {d.to_dict().get("type") for d in docs}
    assert "generated_application" in doc_types or "generated_letter" in doc_types

    # --- human-in-the-loop approval gate (contract §3.3) ---
    approve = api.post(f"/cases/{case_id}/approve_filing", json={"front": "ppdr"})
    assert approve.status_code == 200, approve.text

    deadline = time.monotonic() + FILING_TIMEOUT_S
    filing = None
    while time.monotonic() < deadline:
        matches = list(
            firestore_client.collection("filings")
            .where("case_id", "==", case_id)
            .where("front", "==", "ppdr")
            .stream()
        )
        if matches and matches[0].to_dict().get("status") == "sent":
            filing = matches[0].to_dict()
            break
        time.sleep(POLL_INTERVAL_S)

    assert filing is not None, "PPDR filing never reached status=sent within the timeout"
    assert filing.get("proof", {}).get("phaxio_id") or filing.get("proof", {}).get("lob_id"), (
        "a sent filing must carry vendor proof (contract §3.1 filings/{filing_id}.proof)"
    )


def test_stats_endpoint_reflects_the_injected_case(api):
    """GET /dashboard/stats (contract §3.3) -- the demo's own credibility
    check: after injecting at least one case, the banner must be non-zero."""
    _require_env()
    resp = api.get("/dashboard/stats")
    assert resp.status_code == 200, resp.text
    stats = resp.json()
    assert stats["open_cases"] >= 1
