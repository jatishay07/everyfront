"""Attachment storage -- WO1: "stores raw attachments (PDF bills) to GCS."

Layout: `gs://{GCS_DOCUMENTS_BUCKET}/intake/{gmail_message_id}/{part_id}/{filename}`.

The `{part_id}` segment is not cosmetic. `{message_id}/{filename}` alone is
not unique: one email can carry two PDFs with the same name, and the second
upload silently overwrote the first (in practice it never even got that far --
the dedupe claim, keyed the same way, dropped it first; see
`gmail_client.extract_pdf_attachments`). `partId` is the only identifier Gmail
documents as immutable per part, so one MIME part maps to exactly one object,
forever, on every redelivery.

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


def upload_attachment(
    message_id: str, part_id: str, filename: str, content: bytes, content_type: str
) -> str:
    """Uploads one attachment. Returns its `gs://` URI.

    `part_id` is the MIME part's immutable id (see the module docstring and
    `gmail_client.extract_pdf_attachments`); `filename` stays the object's
    last path segment so a human browsing the bucket still sees `bill.pdf`.
    """
    bucket_name = os.environ["GCS_DOCUMENTS_BUCKET"]
    from google.cloud import storage

    client = storage.Client()
    blob_path = f"intake/{message_id}/{part_id}/{filename}"
    blob = client.bucket(bucket_name).blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{bucket_name}/{blob_path}"
