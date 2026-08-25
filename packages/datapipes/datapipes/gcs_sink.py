"""Upload seed artifacts (CSV, sqlite) to the `ef-datasets` GCS bucket.

Falls back to a no-op + local-path report when GCS isn't reachable, same
philosophy as `firestore_sink.py`: the pipeline should be runnable and
testable without live GCP access.
"""

from __future__ import annotations

from pathlib import Path

from datapipes.config import datasets_bucket


def upload_file(local_path: Path, dest_blob: str, *, dry_run: bool = False) -> str | None:
    """Upload `local_path` to `gs://{datasets_bucket()}/{dest_blob}`.

    Returns the gs:// URI on success, or None if skipped (dry run) or upload
    failed (logged to stderr, never raised -- a dataset upload failure
    should not crash the seed run after Firestore writes already succeeded).
    """
    bucket_name = datasets_bucket()
    if dry_run:
        return None
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(dest_blob)
        blob.upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{dest_blob}"
    except Exception as exc:  # pragma: no cover - network/credential dependent
        import sys

        print(f"[datapipes.gcs_sink] upload of {local_path} failed: {exc}", file=sys.stderr)
        return None
