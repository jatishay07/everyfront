#!/usr/bin/env python3
"""`make demo-reset` -- restore Firestore/GCS to a pristine pre-demo state.

PROOF (persona 7), WO4. Run before every recording take:

    .venv/bin/python fixtures/demo_reset.py            # real reset
    .venv/bin/python fixtures/demo_reset.py --dry-run   # print the plan only

What it does, in order:
  1. Deletes every document in `cases/` (contract §3.1), including the
     `documents/` and `events/` subcollections under each case.
  2. Deletes every document in `filings/`.
  3. Deletes every object under `demo/` in the GCS documents bucket -- NOT
     the whole bucket, so anything outside the demo prefix survives.
  4. Re-seeds `hospitals/{ein}` from fixtures/generated/hospitals.json, so a
     bare or half-populated project always ends up with the same known-good
     hospital records the fixture corpus depends on (LEDGER's real seed
     pipeline, packages/datapipes, is not built yet -- WO1/2 -- so this is
     the fixture-scale stand-in).

Deliberately does NOT touch the demo Google Calendar -- WO4's acceptance
criterion here is scoped to "Firestore/GCS"; wiring the calendar in is
RELAY's territory (packages/delivery, persona 4 WO5) and is left as a
HANDOFF rather than something PROOF reaches into another owner's package for.

Idempotent: running it twice in a row is safe and ends in the same state
(this is exactly WO4's "twice in a row" acceptance test's precondition).

`--dry-run` is fully offline: it needs no GCP credentials, no project, and no
network access at all -- it only prints the fixed plan (which collections get
cleared, which prefix gets wiped, how many hospitals get reseeded from the
committed fixture file). That is what tests/test_demo_harness.py exercises
in CI; the real reset path is exercised by hand during demo rehearsal, since
this sandbox has no live GCP project to reset.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSPITALS_JSON = REPO_ROOT / "fixtures" / "generated" / "hospitals.json"

GCS_DEMO_PREFIX = "demo/"
RESET_COLLECTIONS = ("cases", "filings")


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


def plan(hospitals: dict) -> list[str]:
    """The fixed sequence of actions a real reset performs, in order.

    Pure and offline so both `--dry-run` and the test suite can check it
    without touching GCP.
    """
    bucket_name = os.environ.get("GCS_DOCUMENTS_BUCKET", "ef-documents")
    lines = [
        f"delete all docs in {c}/ (+ their documents/ and events/ subcollections)"
        for c in RESET_COLLECTIONS
    ]
    lines.append(f"delete all objects under gs://{bucket_name}/{GCS_DEMO_PREFIX}")
    lines.append(f"re-seed {len(hospitals)} hospitals/{{ein}} record(s) from {HOSPITALS_JSON.name}")
    return lines


def dry_run() -> None:
    hospitals = json.loads(HOSPITALS_JSON.read_text())
    print("[dry-run] demo-reset plan (no GCP credentials required, nothing touched):")
    for line in plan(hospitals):
        print(f"  - {line}")


def real_reset() -> None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        print(
            "BLOCKED: GOOGLE_CLOUD_PROJECT is not set (see .env.example). "
            "Nothing to reset against.",
            file=sys.stderr,
        )
        sys.exit(1)

    import google.cloud.firestore as firestore
    import google.cloud.storage as storage

    print(f"Resetting project {project!r}...")
    client = firestore.Client(project=project)

    for coll in RESET_COLLECTIONS:
        n = _delete_collection(client, coll)
        print(f"  deleted {n} doc(s) from {coll}/ (+ their documents/ and events/ subcollections)")

    bucket_name = os.environ.get("GCS_DOCUMENTS_BUCKET", "ef-documents")
    gcs = storage.Client(project=project)
    bucket = gcs.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=GCS_DEMO_PREFIX))
    for blob in blobs:
        blob.delete()
    print(f"  deleted {len(blobs)} object(s) under gs://{bucket_name}/{GCS_DEMO_PREFIX}")

    hospitals = json.loads(HOSPITALS_JSON.read_text())
    for ein, record in hospitals.items():
        client.collection("hospitals").document(ein).set(record)
    print(f"  re-seeded {len(hospitals)} hospitals/{{ein}} record(s) from {HOSPITALS_JSON}")

    print("Reset complete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the reset plan without touching GCP or requiring credentials",
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

    real_reset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
