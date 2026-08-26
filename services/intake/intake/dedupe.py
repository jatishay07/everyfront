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

import logging
import os

logger = logging.getLogger("intake.dedupe")


def _bucket_name() -> str:
    return os.environ.get("GCS_DOCUMENTS_BUCKET", "")


def _marker_path(namespace: str, event_id: str) -> str:
    return f"_dedupe/{namespace}/{event_id}.marker"


def claim(namespace: str, event_id: str) -> bool:
    """Atomically claim `(namespace, event_id)`.

    Returns True the first time -- the caller should process the event.
    Returns False on every subsequent call for the same key -- the caller
    should treat this delivery as a no-op and return success to Pub/Sub
    (retrying would only redeliver forever, never converge).

    A claim is provisional until the caller's work SUCCEEDS. Because the
    marker is written before the work happens, a caller that then fails must
    call `release()` -- see its docstring for the failure mode that pairing
    does and does not cover.

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
    blob = client.bucket(bucket_name).blob(_marker_path(namespace, event_id))
    try:
        blob.upload_from_string(b"", if_generation_match=0)
        return True
    except PreconditionFailed:
        return False


def release(namespace: str, event_id: str) -> None:
    """Undo a `claim()` whose work then failed, so a retry can pick it up.

    WHY: `claim()` writes its marker BEFORE the
    caller does any work. If that work then raises -- a transient Gmail
    5xx, a GCS blip, a Pub/Sub publish timeout -- the marker is already
    committed. The route 500s, Pub/Sub redelivers the same `messageId`,
    `claim()` returns False, the handler reports `{"status": "duplicate"}`
    and 200-acks. The attachment is dropped permanently and the log shows one
    traceback followed by a clean success. That is precisely the
    "reported success while doing nothing" shape in HANDOFF.md's bug pattern.

    WHAT THIS DOES NOT COVER: a claim is released only by a caller that lives
    long enough to run its own `except` block. If the process dies between
    the claim and the release -- Cloud Run evicting the instance, an OOM
    kill, the container hitting `--timeout=300`, SIGKILL -- the marker
    survives and that event is still dropped silently on redelivery. Closing
    that hole needs a lease with a TTL (write the marker with an expiry and
    treat an expired marker as unclaimed) or a two-phase commit against
    Firestore, which `ef-intake`'s service account has no grant for (see this
    module's header). This fix converts the common case -- a transient
    downstream error -- from silent data loss into a real retry; it does not
    make the claim crash-safe.

    Best-effort and never raises: a failure to release must not replace the
    original exception the caller is about to re-raise, which is the one that
    actually explains what went wrong.
    """
    bucket_name = _bucket_name()
    if not bucket_name:
        return

    try:
        from google.cloud import storage

        client = storage.Client()
        client.bucket(bucket_name).blob(_marker_path(namespace, event_id)).delete()
    except Exception:  # noqa: BLE001 -- see docstring: must not mask the real error
        logger.warning(
            "could not release dedupe claim %s/%s -- a redelivery of this event will be "
            "treated as a duplicate and skipped",
            namespace,
            event_id,
            exc_info=True,
        )
