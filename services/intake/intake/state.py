"""The Gmail History API cursor -- also stored in GCS for the same IAM
reason as `dedupe.py`.

Gmail's `users.watch` push notification only carries the CURRENT historyId;
finding out what actually changed requires calling `history.list` starting
from the LAST historyId this service successfully processed. That cursor has
to live somewhere between invocations of a scale-to-zero Cloud Run service --
GCS, again, because `ef-intake` has no Firestore grant.

Overwrite semantics, not atomic-claim like `dedupe.py`: at most one Gmail
push per demo account is in flight at a time in practice (single mailbox,
low volume), so a last-write-wins cursor is an acceptable simplification
here. `dedupe.py`'s per-message claim is what actually prevents double
processing if two pushes for the same historyId race.
"""

from __future__ import annotations

import os

_STATE_BLOB = "_state/gmail_last_history_id.txt"


def _bucket_name() -> str:
    return os.environ.get("GCS_DOCUMENTS_BUCKET", "")


def get_last_history_id() -> str | None:
    bucket_name = _bucket_name()
    if not bucket_name:
        return None
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(_STATE_BLOB)
    if not blob.exists():
        return None
    return blob.download_as_text().strip() or None


def set_last_history_id(history_id: str) -> None:
    bucket_name = _bucket_name()
    if not bucket_name:
        return
    from google.cloud import storage

    client = storage.Client()
    client.bucket(bucket_name).blob(_STATE_BLOB).upload_from_string(str(history_id))
