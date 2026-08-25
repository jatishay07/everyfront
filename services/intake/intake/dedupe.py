"""Idempotency on redelivery (agreement §2.3) -- without a Firestore grant.

`ef-intake`'s service account (infra/setup.sh) has `pubsub.publisher`,
`storage.objectAdmin`, and `secretmanager.secretAccessor` -- deliberately NOT
`datastore.user`. This service therefore cannot use Firestore transactions
for dedup the way `services/agent-core` naturally could. Instead, dedup uses
an atomic GCS object create: a marker blob written with
`if_generation_match=0` succeeds only for the FIRST caller to claim a given
`(namespace, event_id)`; every redelivery after that gets a precondition
failure and knows it has already been handled.

This also backs the vendor-webhook -> filing_id lookaside in
`packages/delivery/delivery/vendors/filing.py`, which was written against
the same bucket for the same IAM reason.
"""

from __future__ import annotations

import os


def _bucket_name() -> str:
    return os.environ.get("GCS_DOCUMENTS_BUCKET", "")


def claim(namespace: str, event_id: str) -> bool:
    """Atomically claim `(namespace, event_id)`.

    Returns True the first time -- the caller should process the event.
    Returns False on every subsequent call for the same key -- the caller
    should treat this delivery as a no-op and return success to Pub/Sub
    (retrying would only redeliver forever, never converge).

    Fails OPEN (returns True) if no bucket is configured, e.g. local dev --
    idempotency is a nice-to-have there, but a hard dependency on GCS being
    reachable to even attempt local testing is not.
    """
    bucket_name = _bucket_name()
    if not bucket_name:
        return True

    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(f"_dedupe/{namespace}/{event_id}.marker")
    try:
        blob.upload_from_string(b"", if_generation_match=0)
        return True
    except PreconditionFailed:
        return False
