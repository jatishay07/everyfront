"""Phaxio/Lob status webhooks -> `filing.completed` (contract §3.2).

WO3/WO4: "plus a status callback that publishes filing.completed." This
service is the natural place for it -- it is already the public webhook
receiver for Gmail push, and (like `google_auth.py`) deliberately does NOT
import `packages/delivery/delivery/vendors` even though the parsing logic
duplicates `PhaxioFaxClient.parse_status_callback`/`LobMailClient.
parse_status_callback` almost exactly. See `google_auth.py`'s docstring:
`infra/deploy.sh` scopes each service's Cloud Build context to its own
`services/<name>` directory, so a cross-package import that works under
pytest (via `pyproject.toml`'s `pythonpath`) would fail to build in
production. The vendor-id -> filing-id lookaside blob path
(`_vendor_map/{vendor}/{vendor_id}.json`) is the one piece of shared state
that MUST agree with `packages/delivery/delivery/vendors/filing.py` -- both
read the same GCS bucket by the same convention.
"""

from __future__ import annotations

import json
import os


def _read_vendor_map(vendor: str, vendor_id: str) -> dict | None:
    bucket_name = os.environ.get("GCS_DOCUMENTS_BUCKET", "")
    if not bucket_name:
        return None
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(f"_vendor_map/{vendor}/{vendor_id}.json")
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def _parse_phaxio(payload: dict) -> tuple[str, str]:
    fax = payload.get("fax", payload)
    vendor_id = str(fax.get("id", ""))
    status = {"success": "delivered", "failed": "failed"}.get(
        str(fax.get("status", "")).lower(), "sent"
    )
    return vendor_id, status


def _parse_lob(payload: dict) -> tuple[str, str]:
    body = payload.get("body", payload)
    vendor_id = str(body.get("id", ""))
    event_id = str(payload.get("event_type", {}).get("id", "")).lower()
    if "delivered" in event_id:
        status = "delivered"
    elif any(word in event_id for word in ("rejected", "cancelled", "returned")):
        status = "failed"
    else:
        status = "sent"
    return vendor_id, status


def handle_vendor_callback(channel: str, payload: dict) -> dict[str, str] | None:
    """`channel` is "fax" or "mail". Returns `{"filing_id", "status"}` ready
    to publish as `filing.completed`, or None for an unrecognized/unmapped
    vendor id -- treated as an idempotent no-op by the route handler rather
    than an error (agreement §2.3: a redelivered or stale webhook must not
    5xx and trigger vendor retries forever)."""
    if channel == "fax":
        vendor_name = "phaxio"
        vendor_id, status = _parse_phaxio(payload)
    elif channel == "mail":
        vendor_name = "lob"
        vendor_id, status = _parse_lob(payload)
    else:
        raise ValueError(f"unknown channel {channel!r} (have: fax, mail)")

    if not vendor_id:
        return None
    mapping = _read_vendor_map(vendor_name, vendor_id)
    if mapping is None:
        return None
    return {"filing_id": mapping["filing_id"], "status": status}
