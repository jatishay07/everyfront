"""Write seeded hospital records to Firestore `hospitals/{ein}` (contract §3.1).

Falls back to a local JSONL file when Firestore isn't reachable (no
credentials, no project, offline dev) so the rest of the pipeline -- and its
tests -- never depend on live GCP access. `seed.py --dry-run` forces the
fallback explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from datapipes.select import HospitalCandidate


class HospitalSink(Protocol):
    def write(self, ein: str, record: dict) -> None: ...


def to_contract_record(
    c: HospitalCandidate, *, ccn: str | None, nonprofit: bool | None, mrf_url: str | None
) -> dict:
    """Build the exact `hospitals/{ein}` shape from contract §3.1.

    `nonprofit` at False (for-profit or government) forces `fap_url: None` --
    the product must say "no 501(r) obligation" honestly rather than imply a
    charity-care front that doesn't exist. `nonprofit: None` (unknown, e.g.
    the crosswalk didn't resolve a CCN) leaves the published FAP URL in place
    since Schedule H filers are hospital ORGANIZATIONS filing under 501(r) --
    the filing itself is evidence of nonprofit status.
    """
    fap_url = c.fap_url if nonprofit is not False else None
    tax_year = None
    if c.tax_period_end:
        # tax_period_end is an ISO date ("2023-12-31" per the XML's
        # TaxPeriodEndDt); contract §3.1 wants the filing year, not the date.
        try:
            tax_year = int(c.tax_period_end[:4])
        except ValueError:
            tax_year = c.tax_period_end
    return {
        "name": c.name,
        "ccn": ccn,
        "state": c.state,
        "fap_url": fap_url,
        "fap_app_url": c.fap_app_url if nonprofit is not False else None,
        "free_care_max_fpl_pct": c.free_care_max_fpl_pct,
        "discounted_care_max_fpl_pct": c.discounted_care_max_fpl_pct,
        "source": "schedule_h",
        "tax_year": tax_year,
        "mrf_url": mrf_url,
        "nonprofit": nonprofit,
        # Non-contract, additive metadata -- kept for the demo's "we rebuilt
        # a closed database" narrative and for debugging bad thresholds.
        "facility_count": c.facility_count,
        "facility_names": c.facility_names,
        "url_status": c.url_status,
        "quirks": c.quirks,
    }


class FirestoreSink:
    """Writes to the real `hospitals` collection. Requires ADC or a service account."""

    def __init__(self, project: str | None = None):
        from google.cloud import firestore

        self._client = firestore.Client(project=project)

    def write(self, ein: str, record: dict) -> None:
        self._client.collection("hospitals").document(ein).set(record)


class LocalJsonlSink:
    """Dry-run / offline fallback: append-or-replace records in a JSONL file."""

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = {}
        if self._path.exists():
            for line in self._path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._records[row["_ein"]] = row

    def write(self, ein: str, record: dict) -> None:
        self._records[ein] = {"_ein": ein, **record}
        self._flush()

    def _flush(self) -> None:
        with open(self._path, "w") as fh:
            for row in self._records.values():
                fh.write(json.dumps(row) + "\n")


def get_sink(*, dry_run: bool, local_path: Path, project: str | None = None) -> HospitalSink:
    """Real Firestore unless `dry_run`, or Firestore init fails (offline dev)."""
    if not dry_run:
        try:
            return FirestoreSink(project=project)
        except Exception:
            pass
    return LocalJsonlSink(local_path)
