#!/usr/bin/env python3
"""`make demo-reset` -- restore Firestore/GCS to a pristine pre-demo state,
then reseed the 8-case corpus with human-plausible case identifiers.

PROOF (persona 7), WO4 + WO6 task 1. Run before every recording take:

    .venv/bin/python fixtures/demo_reset.py                     # purge only
    .venv/bin/python fixtures/demo_reset.py --reseed             # purge + reseed
    .venv/bin/python fixtures/demo_reset.py --dry-run             # print the plan only
    .venv/bin/python fixtures/demo_reset.py --dry-run --reseed    # plan incl. reseed

`make demo-reset` (see fixtures/Makefile) passes `--reseed`, so the make
target itself always does both; the bare script defaults to purge-only so
`tests/test_demo_harness.py`'s existing "blocked without GOOGLE_CLOUD_PROJECT"
check (which never sets EVERYFRONT_API_URL either) keeps working unchanged.

What the PURGE does, in order:
  1. Deletes every document in `cases/` (contract §3.1), including the
     `documents/` and `events/` subcollections under each case.
  2. Deletes every document in `filings/`.
  3. Deletes every object under `demo/` in the GCS documents bucket -- NOT
     the whole bucket, so anything outside the demo prefix survives.
  4. Verifies (read-only) that this corpus's hospital EINs already exist in
     `hospitals/{ein}` -- logs a warning per missing EIN, but never writes.

AMENDED 2026-08-25 (live-verification pass against the deployed project):
this used to ALSO re-seed `hospitals/{ein}` from fixtures/generated/
hospitals.json as a "fixture-scale stand-in" for LEDGER's not-yet-built real
seed pipeline. That pipeline shipped for real (packages/datapipes WO1-3,
200 real hospitals from IRS Schedule H) and running `.set()` over the same
4 EINs from this corpus's minimal fixture record would silently downgrade
whatever richer/more accurate fields LEDGER's real pipeline wrote (ccn, mrf
data, a fresher tax_year, etc.) back to this fixture's placeholder values --
and FORGE flagged explicitly: never touch `hospitals/` here, re-seeding it
for real means re-downloading gigabytes of IRS filings. Verify-only, never
write, from this point on.

WHAT `--reseed` DOES (WO6 task 1, "reseed your 8 cases with human-plausible
case identifiers, not demo-<fixture>-<uuid>"):

`/demo/inject_bill` (services/api/main.py -- SWARM's file, outside `fixtures/`
and `tests/`, so PROOF does not edit it directly per BUILD_PLAYBOOK.md §0.2)
always names a freshly-injected case `demo-{fixture_name}-{uuid8}`. That is
exactly the "reads like scratch data" problem this work order calls out. Only
7 of the corpus's 8 cases are reseeded here -- `case_01_uninsured_gfe_ca` (see
`LIVE_DEMO_FIXTURE`) is deliberately left for `fixtures/demo_run.py` to inject
LIVE, on camera, during the recorded take; reseeding it here too would leave
9 cases in Firestore once the recording finishes, one case short of (or one
over) the §7 "8 cases" banner target depending on how you count. Rather than
touch `services/api/main.py`'s case_id generation, this module runs each
background fixture through the real, live pipeline via `/demo/inject_bill`
(so hospital resolution, deadlines, fronts, and filings are all genuinely
computed by the live system, exactly as they would be for the flagship case),
then performs a same-shape Firestore copy-and-delete -- `rename_case` below --
to give the finished case a human-plausible id. `demo_run.py` runs the
identical `rename_case` step on its own live-injected case, so the id on
screen during the recorded segment is just as plausible as the seven quiet
ones. `POST /cases/{id}/approve_filing` and `GET /cases/{id}` both look the
case up by bare Firestore document id (`services/api/api_core/store.py`) with
no assumption about its format, so the rename is invisible to every other
consumer -- confirmed by reading that module before relying on it.

Each fixture takes on the order of a minute or two through the live pipeline
(agent-core's Reader/Lookup/Clock/Auditor/Strategist cascade, then one
`approve_filing` round-trip per applicable front) -- reseeding all 7 runs
several minutes. That is a pre-recording setup cost, not part of the timed
on-camera segment (`fixtures/demo_run.py`'s own 4-minute budget covers only
the ONE case it injects live).

HANDOFF -> SWARM: the real fix is for `/demo/inject_bill` itself to assign a
human-plausible case_id (services/api/main.py's `inject_bill`) instead of
`demo-{fixture_name}-{uuid8}` -- this module's copy-and-delete dance exists
only because that file is outside PROOF's owned paths.

Deliberately does NOT touch the demo Google Calendar -- WO4's acceptance
criterion here is scoped to "Firestore/GCS"; wiring the calendar in is
RELAY's territory (packages/delivery, persona 4 WO5) and is left as a
HANDOFF rather than something PROOF reaches into another owner's package for.

Idempotent: running it twice in a row is safe and ends in the same state
(this is exactly WO4's "twice in a row" acceptance test's precondition) --
`--reseed` always starts from a purge, so a fixture never collides with a
same-named leftover from a previous run.

`--dry-run` is fully offline: it needs no GCP credentials, no project, and no
network access at all -- it only prints the fixed plan (which collections get
cleared, which prefix gets wiped, which hospital EINs get checked, which
fixtures get reseeded under which ids). That is what tests/test_demo_harness.py
exercises in CI; the real reset/reseed path is exercised against the live
deployed project during demo rehearsal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSPITALS_JSON = REPO_ROOT / "fixtures" / "generated" / "hospitals.json"

GCS_DEMO_PREFIX = "demo/"
RESET_COLLECTIONS = ("cases", "filings")

# Deterministic, human-plausible case identifiers for the 8-case corpus --
# assigned once, in fixtures/cases_data.py's own CASES order. A judge reads
# these on screen; "ef-2026-000N" reads like a real case-tracking scheme, not
# a fixture name with a random suffix. This mapping is the single source of
# truth for both `demo_reset.py --reseed` (the 7 background cases) and
# `demo_run.py` (the 8th, injected live).
HUMAN_CASE_IDS: dict[str, str] = {
    "case_01_uninsured_gfe_ca": "ef-2026-0001",
    "case_02_wrongful_denial_il": "ef-2026-0002",
    "case_03_in_collections_ca": "ef-2026-0003",
    "case_04_forprofit_il": "ef-2026-0004",
    "case_05_cat_photo_income_proof": "ef-2026-0005",
    "case_06_unparseable_bill": "ef-2026-0006",
    "case_07_il_concurrent_clocks": "ef-2026-0007",
    "case_08_lawful_denial_ca": "ef-2026-0008",
}

# fixtures/demo_run.py's own flagship fixture -- injected live, on camera, so
# it is deliberately excluded from the quiet pre-recording reseed below.
LIVE_DEMO_FIXTURE = "case_01_uninsured_gfe_ca"
RESEED_FIXTURES: tuple[str, ...] = tuple(f for f in HUMAN_CASE_IDS if f != LIVE_DEMO_FIXTURE)

INJECT_TIMEOUT_S = float(os.environ.get("EVERYFRONT_INJECT_TIMEOUT_S", "360"))
FILING_TIMEOUT_S = 90


def _log(t0: float, msg: str) -> None:
    print(f"[{time.monotonic() - t0:6.1f}s] {msg}")


def _delete_collection(client, name: str, batch_size: int = 200) -> int:
    coll = client.collection(name)
    deleted = 0
    while True:
        docs = list(coll.limit(batch_size).stream())
        if not docs:
            break
        for doc in docs:
            for sub in ("documents", "events"):
                _delete_collection(client, f"{name}/{doc.id}/{sub}")
            doc.reference.delete()
            deleted += 1
    return deleted


def _copy_subcollection(old_ref, new_ref, name: str, *, new_case_id: str | None = None) -> int:
    """Copy every doc in `old_ref`'s `name` subcollection to `new_ref`,
    preserving doc ids. When `new_case_id` is given, each copied doc's own
    `case_id` field (events carry one; contract §3.1) is rewritten to match
    -- otherwise the global `/events` feed would show events whose embedded
    `case_id` no longer matches the case they actually live under."""
    n = 0
    for snap in old_ref.collection(name).stream():
        data = snap.to_dict()
        if new_case_id is not None and "case_id" in data:
            data["case_id"] = new_case_id
        new_ref.collection(name).document(snap.id).set(data)
        n += 1
    return n


def rename_case(client, old_id: str, new_id: str) -> None:
    """Give `cases/{old_id}` a new, human-plausible document id.

    Copies the case doc + its `documents/` and `events/` subcollections to
    `cases/{new_id}` (rewriting each event's own `case_id` field to match),
    repoints any `filings/{filing_id}.case_id` that referenced `old_id`, then
    deletes the `old_id` doc + subcollections. See this module's docstring
    for why this exists instead of changing `/demo/inject_bill`'s own id
    scheme.

    `filings/{filing_id}` documents are patched in place (`case_id` field
    only) rather than copied -- their own doc id is never shown on screen and
    has no meaning outside this contract, so there is nothing to rename.
    """
    old_ref = client.collection("cases").document(old_id)
    old_snap = old_ref.get()
    if not old_snap.exists:
        raise RuntimeError(f"rename_case: cases/{old_id} does not exist")

    new_ref = client.collection("cases").document(new_id)
    new_ref.set(old_snap.to_dict())
    _copy_subcollection(old_ref, new_ref, "documents")
    _copy_subcollection(old_ref, new_ref, "events", new_case_id=new_id)

    for snap in client.collection("filings").where("case_id", "==", old_id).stream():
        snap.reference.update({"case_id": new_id})

    for sub in ("documents", "events"):
        _delete_collection(client, f"cases/{old_id}/{sub}")
    old_ref.delete()


def _approve_all_applicable(api, case_id: str, case: dict) -> list[str]:
    """Approve every applicable front on `case_id` (contract §3.3's
    human-in-the-loop gate). Returns the fronts actually approved; a front
    the Verifier blocks (409) is logged and skipped, not treated as fatal --
    the reseed's job is to populate the corpus, not to force a filing past a
    genuine pre-filing check."""
    approved: list[str] = []
    for front in case.get("fronts") or []:
        if not front.get("applicable"):
            continue
        r = api.post(f"/cases/{case_id}/approve_filing", json={"front": front["front"]})
        if r.status_code == 200:
            approved.append(front["front"])
        else:
            reason = (
                r.json().get("detail", r.text)
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            )
            print(
                f"    WARNING: front={front['front']} not approved ({r.status_code}): {reason}",
                file=sys.stderr,
            )
    return approved


def reseed_plan_lines() -> list[str]:
    """The fixed sequence `--reseed` performs, in order. Pure and offline."""
    lines = [
        f"reseed {len(RESEED_FIXTURES)} background case(s) quietly via /demo/inject_bill, "
        f"approving every applicable front on each:"
    ]
    for fixture_name in RESEED_FIXTURES:
        lines.append(f"  {fixture_name} -> rename to {HUMAN_CASE_IDS[fixture_name]}")
    lines.append(
        f"leave {LIVE_DEMO_FIXTURE!r} unseeded -- fixtures/demo_run.py injects it LIVE, on "
        f"camera, then renames it to {HUMAN_CASE_IDS[LIVE_DEMO_FIXTURE]} the same way"
    )
    return lines


def reseed(api_url: str, project: str) -> int:
    import google.cloud.firestore as firestore
    import httpx

    fs_client = firestore.Client(project=project)
    api = httpx.Client(base_url=api_url.rstrip("/"), timeout=30.0)

    t0 = time.monotonic()
    for fixture_name in RESEED_FIXTURES:
        target_id = HUMAN_CASE_IDS[fixture_name]
        _log(t0, f"seeding {fixture_name!r} (-> {target_id})...")
        resp = api.post(
            "/demo/inject_bill", json={"fixture_name": fixture_name}, timeout=INJECT_TIMEOUT_S
        )
        resp.raise_for_status()
        raw_id = resp.json()["case_id"]

        r = api.get(f"/cases/{raw_id}")
        r.raise_for_status()
        case = r.json()
        _log(t0, f"  analyzed -- {len(case.get('fronts', []))} front(s) selected")

        approved = _approve_all_applicable(api, raw_id, case)
        if approved:
            deadline = time.monotonic() + FILING_TIMEOUT_S
            while time.monotonic() < deadline:
                r = api.get(f"/cases/{raw_id}")
                r.raise_for_status()
                case = r.json()
                statuses = {f["front"]: f.get("status") for f in case.get("fronts", [])}
                if all(statuses.get(f) == "filed" for f in approved):
                    break
                time.sleep(5)
            else:
                print(
                    f"    WARNING: {raw_id} did not reach status=filed on every approved "
                    f"front within {FILING_TIMEOUT_S}s",
                    file=sys.stderr,
                )
        rename_case(fs_client, raw_id, target_id)
        _log(t0, f"  renamed {raw_id} -> {target_id} ({len(approved)} filing(s) approved)")

    _log(t0, f"reseed complete: {len(RESEED_FIXTURES)} case(s) seeded")
    return 0


def _default_bucket_name() -> str:
    """The documents bucket to purge -- `GCS_DOCUMENTS_BUCKET` if set,
    otherwise infra/setup.sh's actual naming convention
    (`ef-documents-${PROJECT_ID}`, infra/setup.sh:118), NOT the bare
    `ef-documents` this used to fall back to. That bucket does not exist in
    the deployed project (confirmed live, 2026-08-25: only
    `ef-documents-everyfront-hack-2026`, `ef-datasets-everyfront-hack-2026`,
    and a Cloud Run source-staging bucket exist) -- an operator who forgot to
    export GCS_DOCUMENTS_BUCKET got a 404 NotFound instead of a purge.
    Falls back to the bare name only when GOOGLE_CLOUD_PROJECT itself isn't
    set either, which only happens for `plan()`'s offline --dry-run display
    (nothing is touched there regardless).
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    default = f"ef-documents-{project}" if project else "ef-documents"
    return os.environ.get("GCS_DOCUMENTS_BUCKET", default)


def plan(hospitals: dict) -> list[str]:
    """The fixed sequence of actions a real reset (+ reseed) performs, in
    order. Pure and offline so both `--dry-run` and the test suite can check
    it without touching GCP.
    """
    bucket_name = _default_bucket_name()
    lines = [
        f"delete all docs in {c}/ (+ their documents/ and events/ subcollections)"
        for c in RESET_COLLECTIONS
    ]
    lines.append(f"delete all objects under gs://{bucket_name}/{GCS_DEMO_PREFIX}")
    lines.append(
        f"verify (read-only, never write) that {len(hospitals)} hospitals/{{ein}} "
        f"record(s) from {HOSPITALS_JSON.name} already exist in Firestore"
    )
    lines.extend(reseed_plan_lines())
    return lines


def dry_run() -> None:
    hospitals = json.loads(HOSPITALS_JSON.read_text())
    print("[dry-run] demo-reset plan (no GCP credentials required, nothing touched):")
    for line in plan(hospitals):
        print(f"  - {line}")


def real_reset(*, do_reseed: bool) -> int:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        print(
            "BLOCKED: GOOGLE_CLOUD_PROJECT is not set (see .env.example). "
            "Nothing to reset against.",
            file=sys.stderr,
        )
        return 1

    api_url = os.environ.get("EVERYFRONT_API_URL", "").rstrip("/")
    if do_reseed and not api_url:
        print(
            "BLOCKED: --reseed needs EVERYFRONT_API_URL set (contract §3.3) -- the "
            "purge below would still run, but reseeding calls the live /demo/inject_bill "
            "endpoint, which needs a deployed services/api URL. Nothing was touched.",
            file=sys.stderr,
        )
        return 1

    import google.cloud.firestore as firestore
    import google.cloud.storage as storage

    print(f"Resetting project {project!r}...")
    client = firestore.Client(project=project)

    for coll in RESET_COLLECTIONS:
        n = _delete_collection(client, coll)
        print(f"  deleted {n} doc(s) from {coll}/ (+ their documents/ and events/ subcollections)")

    bucket_name = _default_bucket_name()
    gcs = storage.Client(project=project)
    bucket = gcs.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=GCS_DEMO_PREFIX))
    for blob in blobs:
        blob.delete()
    print(f"  deleted {len(blobs)} object(s) under gs://{bucket_name}/{GCS_DEMO_PREFIX}")

    # Read-only check. NEVER write here -- see the module docstring's 2026-08-25
    # amendment: LEDGER's real 200-hospital Schedule H seed owns this
    # collection now, and clobbering it with this corpus's 4-hospital
    # placeholder record would be a real data-loss regression, not a reset.
    hospitals = json.loads(HOSPITALS_JSON.read_text())
    missing = [
        ein for ein in hospitals if not client.collection("hospitals").document(ein).get().exists
    ]
    if missing:
        print(
            f"  WARNING: {len(missing)} hospital EIN(s) this corpus depends on are "
            f"missing from hospitals/: {missing}. Not writing them -- re-run "
            "LEDGER's seed pipeline (packages/datapipes) instead of trusting this "
            "script to fabricate hospital records.",
            file=sys.stderr,
        )
    else:
        print(f"  verified {len(hospitals)} hospitals/{{ein}} record(s) already present")

    print("Purge complete.")

    if not do_reseed:
        return 0

    try:
        import httpx  # noqa: F401
    except ImportError:
        print(
            "BLOCKED: httpx is not installed, needed for --reseed. Run "
            "`pip install -r fixtures/requirements.txt`. The purge above already ran.",
            file=sys.stderr,
        )
        return 1

    print(f"Reseeding {len(RESEED_FIXTURES)} background case(s) against {api_url!r}...")
    return reseed(api_url, project)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the reset (+ reseed) plan without touching GCP or requiring credentials",
    )
    parser.add_argument(
        "--reseed",
        action="store_true",
        help=(
            "after purging, reseed the 7 background cases against a live deployment "
            "(needs EVERYFRONT_API_URL in addition to GOOGLE_CLOUD_PROJECT). "
            "`make demo-reset` always passes this; the bare script defaults to purge-only."
        ),
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        dry_run()
        return 0

    if not os.environ.get("GOOGLE_CLOUD_PROJECT", ""):
        print(
            "BLOCKED: GOOGLE_CLOUD_PROJECT is not set (see .env.example). "
            "Nothing to reset against.",
            file=sys.stderr,
        )
        return 1

    try:
        import google.cloud.firestore  # noqa: F401
        import google.cloud.storage  # noqa: F401
    except ImportError:
        print(
            "google-cloud-firestore / google-cloud-storage not installed. "
            "Install fixtures/requirements.txt, or run with --dry-run to "
            "check the script's plan without them.",
            file=sys.stderr,
        )
        return 1

    return real_reset(do_reseed=args.reseed)


if __name__ == "__main__":
    raise SystemExit(main())
