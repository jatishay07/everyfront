#!/usr/bin/env python3
"""`make demo-run` -- execute the happy path against a live deployment.

PROOF (persona 7), WO4. Run after `demo-reset`:

    .venv/bin/python fixtures/demo_run.py            # real run
    .venv/bin/python fixtures/demo_run.py --dry-run   # print the script, time nothing

Steps (contract §3.3 / §3.1), timed end-to-end against the acceptance
criterion of "under 4 minutes of watchable action":

  1. POST /demo/inject_bill {"fixture_name": "case_01_uninsured_gfe_ca"} --
     the demo's own happy path (uninsured + GFE + CA: PPDR + charity care,
     no charity-care deadline drama, nothing that should legitimately block
     a filing).
  2. Poll GET /cases/{id} until status reaches strategy_ready (or later).
  3. POST /cases/{id}/approve_filing for each selected front.
  4. Poll GET /cases/{id} until every approved front's status is "filed".
  5. GET /dashboard/stats and print the banner.

`--dry-run` needs no live API and prints the same step sequence with the
fixture's own precomputed `expected` block standing in for what a live run
would report -- useful for rehearsing narration timing without a deployment,
and what tests/test_demo_harness.py exercises in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASE_JSON = (
    REPO_ROOT / "fixtures" / "generated" / "cases" / "case_01_uninsured_gfe_ca" / "case.json"
)

# Persona 7 WO4 acceptance: the full happy path, twice in a row, under 4
# minutes of watchable action.
BUDGET_S = 4 * 60
ANALYSIS_TIMEOUT_S = 180  # persona 5 WO1 acceptance: "within 3 minutes"
FILING_TIMEOUT_S = 60
POLL_INTERVAL_S = 5


def _log(t0: float, msg: str) -> None:
    print(f"[{time.monotonic() - t0:6.1f}s] {msg}")


def dry_run() -> int:
    case = json.loads(CASE_JSON.read_text())
    t0 = time.monotonic()
    _log(t0, f"(dry-run) would inject fixture {case['case_id']!r}: {case['title']}")
    for front in case["expected"]["fronts_reference_model"]:
        mark = "->" if front["applicable"] else "-- (not applicable)"
        _log(t0, f"(dry-run) front {front['front']} {mark} {front['reason']}")
    for d in case["expected"]["deadlines"]:
        _log(t0, f"(dry-run) deadline: {d['name']} due {d['due']} ({d['citation']})")
    audit_total = case["expected"]["audit_findings_cents_total"] / 100
    _log(t0, f"(dry-run) audit findings total: ${audit_total:,.2f}")
    _log(t0, "(dry-run) would approve every applicable front, then poll for filed status")
    _log(t0, f"(dry-run) plan complete -- real run budget is {BUDGET_S}s")
    return 0


def real_run() -> int:
    # Config check BEFORE the optional import. Reversed, a missing httpx
    # produces a traceback instead of this message -- and on demo day the
    # person running this needs to be told what to set, not shown a stack.
    api_url = os.environ.get("EVERYFRONT_API_URL", "").rstrip("/")
    if not api_url:
        print(
            "BLOCKED: EVERYFRONT_API_URL is not set. Point it at the deployed "
            "services/api Cloud Run URL (contract §3.3) before running the "
            "real demo harness.",
            file=sys.stderr,
        )
        return 1

    try:
        import httpx
    except ModuleNotFoundError:
        print(
            "BLOCKED: httpx is not installed. Run "
            "`pip install -r fixtures/requirements.txt` before the real demo harness.",
            file=sys.stderr,
        )
        return 1

    case = json.loads(CASE_JSON.read_text())
    fixture_name = case["case_id"]
    t0 = time.monotonic()
    client = httpx.Client(base_url=api_url, timeout=30.0)

    _log(t0, f"injecting fixture {fixture_name!r}...")
    resp = client.post("/demo/inject_bill", json={"fixture_name": fixture_name})
    resp.raise_for_status()
    case_id = resp.json()["case_id"]
    _log(t0, f"case {case_id} created")

    deadline = time.monotonic() + ANALYSIS_TIMEOUT_S
    data = None
    while time.monotonic() < deadline:
        r = client.get(f"/cases/{case_id}")
        r.raise_for_status()
        data = r.json()
        if data.get("status") in ("strategy_ready", "filing", "awaiting_response"):
            break
        time.sleep(POLL_INTERVAL_S)
    else:
        print(f"BLOCKED: case {case_id} never reached strategy_ready", file=sys.stderr)
        return 1
    _log(t0, f"analysis complete -- {len(data.get('fronts', []))} front(s) selected")
    for front in data.get("fronts", []):
        _log(t0, f"  front {front['front']}: {front.get('reason', '')}")

    applicable_fronts = [f["front"] for f in data.get("fronts", []) if f.get("applicable")]
    for front in applicable_fronts:
        _log(t0, f"approving filing for front={front}...")
        r = client.post(f"/cases/{case_id}/approve_filing", json={"front": front})
        r.raise_for_status()

    deadline = time.monotonic() + FILING_TIMEOUT_S
    while time.monotonic() < deadline:
        r = client.get(f"/cases/{case_id}")
        r.raise_for_status()
        data = r.json()
        fronts_by_name = {f["front"]: f for f in data.get("fronts", [])}
        if all(fronts_by_name.get(f, {}).get("status") == "filed" for f in applicable_fronts):
            break
        time.sleep(POLL_INTERVAL_S)
    else:
        print("BLOCKED: not every approved front reached status=filed", file=sys.stderr)
        return 1
    _log(t0, "all approved fronts filed")

    stats = client.get("/dashboard/stats")
    stats.raise_for_status()
    _log(t0, f"dashboard stats: {json.dumps(stats.json())}")

    elapsed = time.monotonic() - t0
    _log(t0, f"DONE in {elapsed:.1f}s (budget {BUDGET_S}s)")
    if elapsed > BUDGET_S:
        print(
            f"WARNING: exceeded the {BUDGET_S}s demo budget -- see WO4 acceptance",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the run script against the fixture's precomputed expectations, no API needed",
    )
    args = parser.parse_args(argv)
    return dry_run() if args.dry_run else real_run()


if __name__ == "__main__":
    raise SystemExit(main())
