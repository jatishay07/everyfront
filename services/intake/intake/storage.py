"""Attachment storage -- WO1: "stores raw attachments (PDF bills) to GCS."

Layout: `gs://{GCS_DOCUMENTS_BUCKET}/intake/{gmail_message_id}/{filename}`.

Landing under `intake/{message_id}/...` rather than `cases/{case_id}/...` is
deliberate: this service does not have a Firestore grant (see
`dedupe.py`'s docstring) and therefore cannot look up or create the
`cases/{case_id}` document that would make a case-scoped path meaningful --
that is Reader's (SWARM, agent-core) job on `case.document.added`. See the
PR HANDOFF for this assumption; it's the one place this service's scope
leans on a downstream persona filling in a gap `ef-intake`'s IAM can't reach.
"""

from __future__ import annotations

import os


def upload_attachment(message_id: str, filename: str, content: bytes, content_type: str) -> str:
    """Uploads one attachment. Returns its `gs://` URI."""
    bucket_name = os.environ["GCS_DOCUMENTS_BUCKET"]
    from google.cloud import storage

    client = storage.Client()
    blob_path = f"intake/{message_id}/{filename}"
    blob = client.bucket(bucket_name).blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{bucket_name}/{blob_path}"
