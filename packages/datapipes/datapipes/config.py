"""Environment-driven configuration for the datapipes package.

Mirrors the env var names in `.env.example` (GOOGLE_CLOUD_PROJECT,
GCS_DATASETS_BUCKET). `infra/setup.sh` (ATLAS, persona 1) creates the real
bucket as ``{GCS_DATASETS_BUCKET}-{GOOGLE_CLOUD_PROJECT}`` -- see
`infra/setup.sh` line ~91 -- so `datasets_bucket()` reproduces that suffixing
rather than duplicating the literal name.
"""

from __future__ import annotations

import os


def project_id() -> str | None:
    """Resolve the active GCP project.

    Prefers the explicit env vars (matches `.env.example` / how the deployed
    services get configured). Falls back to Application Default Credentials'
    own project detection (gcloud config / metadata server) so a local run
    from an authenticated `gcloud` shell -- like this pipeline's seed CLI --
    doesn't need the env var set to find the right bucket.
    """
    explicit = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    if explicit:
        return explicit
    try:
        import google.auth

        _, adc_project = google.auth.default()
        return adc_project
    except Exception:
        return None


def datasets_bucket() -> str:
    base = os.environ.get("GCS_DATASETS_BUCKET", "ef-datasets")
    pid = project_id()
    # infra/setup.sh names the real bucket "{base}-{project}"; if the caller
    # already passed the fully-qualified name (contains the project id),
    # don't double-suffix it.
    if pid and not base.endswith(pid):
        return f"{base}-{pid}"
    return base


def documents_bucket() -> str:
    base = os.environ.get("GCS_DOCUMENTS_BUCKET", "ef-documents")
    pid = project_id()
    if pid and not base.endswith(pid):
        return f"{base}-{pid}"
    return base
