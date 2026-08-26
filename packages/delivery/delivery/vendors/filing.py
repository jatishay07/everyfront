"""Orchestrator: pick the right vendor client, send, and make the result
resolvable from a vendor webhook later.

Contract §3.1 `filings/{filing_id}` and §3.2 `filing.completed` both key off
`filing_id`, but a vendor webhook only ever hands back ITS OWN id (a Phaxio
fax id or a Lob letter id). `_write_vendor_map`/`read_vendor_map` is the
lookaside that closes that gap: a tiny JSON object written to the same GCS
bucket `services/intake` already has `storage.objectAdmin` on (see
infra/setup.sh's `ef-intake` service account), so resolving a callback never
requires a new IAM grant. Best-effort by design -- a mapping write failure
must never fail the filing itself; the filing already succeeded with the
vendor by the time this runs.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import VendorClient, VendorResult
from .fax import PhaxioFaxClient
from .mail import LobMailClient


def get_fax_client() -> VendorClient:
    return PhaxioFaxClient()


def get_mail_client() -> VendorClient:
    return LobMailClient()


def _vendor_map_blob_path(vendor: str, vendor_id: str) -> str:
    return f"_vendor_map/{vendor}/{vendor_id}.json"


def _write_vendor_map(vendor: str, vendor_id: str, filing_id: str, case_id: str) -> None:
    bucket_name = os.environ.get("GCS_DOCUMENTS_BUCKET", "")
    if not bucket_name:
        return  # no bucket configured (local dev/tests) -- skip silently
    try:
        from google.cloud import storage

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(_vendor_map_blob_path(vendor, vendor_id))
        blob.upload_from_string(
            json.dumps({"filing_id": filing_id, "case_id": case_id}),
            content_type="application/json",
        )
    except Exception:  # noqa: BLE001 -- best-effort side channel only
        pass


def read_vendor_map(vendor: str, vendor_id: str) -> dict[str, Any] | None:
    bucket_name = os.environ.get("GCS_DOCUMENTS_BUCKET", "")
    if not bucket_name:
        return None
    try:
        from google.cloud import storage

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(_vendor_map_blob_path(vendor, vendor_id))
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception:  # noqa: BLE001
        return None


def send_filing(
    *,
    filing_id: str,
    case_id: str,
    front: str,
    channel: str,
    pdf: bytes,
    destination: Any,
    fax_client: VendorClient | None = None,
    mail_client: VendorClient | None = None,
) -> dict[str, Any]:
    """Send one filing. Returns a dict shaped for `filings/{filing_id}`
    (contract §3.1) -- the Filer (SWARM, agent-core) writes it to Firestore
    and appends the matching `events/` entry; this function's job stops at
    "sent, here is the proof."
    """
    if channel == "fax":
        client = fax_client or get_fax_client()
    elif channel == "mail":
        client = mail_client or get_mail_client()
    else:
        raise ValueError(f"unknown channel {channel!r} (have: fax, mail)")

    result: VendorResult = client.send(filing_id, pdf, destination)
    _write_vendor_map(result.vendor, result.vendor_id, filing_id, case_id)
    return {
        "case_id": case_id,
        "front": front,
        "channel": channel,
        "vendor": result.vendor,
        "vendor_id": result.vendor_id,
        "status": result.status,
        "proof": result.proof,
        "sent_at": result.sent_at.isoformat(),
        # BUG FOUND live (RELAY WO8, item 2 spot-check): `agent_core.agents.filer`
        # (services/agent-core) has read `vendor_result.get("simulated")` since
        # persona 5 WO6 -- both to build `filings/{filing_id}`'s own `simulated`
        # flag and to pick "SIMULATED" vs "live" in the human-readable event
        # narration (`pipeline.py`'s `run_filer`) -- but this dict never
        # actually carried a `"simulated"` key, so `.get("simulated")` was
        # always `None` -> `bool(None)` == `False`. Every filing that fell back
        # to `FakeFaxVendor`/`FakeMailVendor` (i.e. every filing, absent real
        # Phaxio/Lob keys -- see this package's README) was therefore logged
        # and stored as `simulated: false` / narrated as "live" even though
        # `vendor == "fake"` right there in the same dict said otherwise. This
        # is the exact "reports success while it's actually a placeholder"
        # failure mode BUILD_PLAYBOOK.md's WO8 brief calls out by name -- the
        # two facts (`vendor` and `simulated`) had simply never been made to
        # agree. `vendor` is the one fact this function actually computes
        # (`result.vendor` comes straight from whichever `VendorClient` ran,
        # "fake" | "phaxio" | "lob"); `simulated` is now derived from it
        # instead of being a second, independently-settable value that could
        # drift from the first.
        "simulated": result.vendor == "fake",
    }


def handle_status_callback(channel: str, payload: dict) -> dict[str, str] | None:
    """Vendor webhook body -> {"filing_id": ..., "status": ...}, the exact
    shape `filing.completed` (contract §3.2) publishes.

    Returns None when the vendor_id is unfamiliar (e.g. a callback for a
    filing this deployment never sent, or a redelivered webhook after the
    lookaside expired) -- the caller should treat that as a no-op, not an
    error, which is what makes the webhook handler idempotent on redelivery
    (agreement §2.3).
    """
    if channel == "fax":
        client: VendorClient = PhaxioFaxClient()
        vendor_name = "phaxio"
    elif channel == "mail":
        client = LobMailClient()
        vendor_name = "lob"
    else:
        raise ValueError(f"unknown channel {channel!r} (have: fax, mail)")

    vendor_id, status = client.parse_status_callback(payload)
    if not vendor_id:
        return None
    mapping = read_vendor_map(vendor_name, vendor_id)
    if mapping is None:
        return None
    return {"filing_id": mapping["filing_id"], "status": status}
