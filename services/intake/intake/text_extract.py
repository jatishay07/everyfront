"""Best-effort PDF -> text extraction, run INSIDE this service at
attachment-fetch time.

WHY THIS EXISTS (WO7, RELAY -- closing the gap FORGE flagged as the most
important finding of this work order): `case.document.added` used to publish
`{case_id, doc_id, gcs_uri, filename, gmail_message_id, gmail_thread_id}` --
never any text. Downstream, `agent_core.pipeline._run_reader` calls
`reader.run(case_id, doc_id, doc.get("raw_text", ""), doc.get("type"))`: it
reads `raw_text` off the Firestore `documents/{doc_id}` record. Nothing in the
live pipeline ever put text there for a real, Gmail-sourced PDF -- the ONLY
place that ever populates `raw_text` is `fixtures/build.py`, offline, at
fixture-build time, for PROOF's synthetic corpus. A real emailed bill would
have landed in GCS, published its event, and then gone nowhere: Reader would
run against an empty string and classify/extract nothing.

Extracting here, and shipping the text IN the Pub/Sub payload, means the
HANDOFF this PR leaves for SWARM (see the PR description) is "store this text
you were already given" -- not "also add a PDF parser and a new GCS read
grant to agent-core." `ef-intake`'s service account already has
`storage.objectAdmin` (this service uploaded the bytes two lines earlier);
`ef-agent` would otherwise need the same round-trip to GCS just to get back
text this service already had in memory.

`pypdf` is imported lazily -- same reasoning as every other optional-heavy
import in this codebase (see `packages/delivery/delivery/pdf/engine.py`'s
module docstring): a top-level `import pypdf` here would turn "not installed
in this environment" into "the whole test suite fails to collect" for anyone
running pytest without it.
"""

from __future__ import annotations

import io

# A generous ceiling, not a real limit any real bill approaches: Pub/Sub caps
# a message at 10MB total, and this is one field of one JSON payload among
# several. This guards against the pathological case (a mis-attached scanned
# book, a PDF bomb) turning an attachment fetch into an oversized publish that
# Pub/Sub would reject outright -- degrade to a clipped extract, never crash.
MAX_CHARS = 200_000


def extract_pdf_text(content: bytes) -> str:
    """Best-effort text extraction from raw PDF bytes. Returns `""` (never
    raises) on anything pypdf cannot parse -- an encrypted, scanned-with-no-
    text-layer, or corrupt PDF. Reader already treats an empty `raw_text` as
    "nothing to extract" rather than crashing on it (see its
    `is_income_proof`/`_extraction_error` handling), and a bill that fails to
    parse should still land as a document a human can open from GCS/Drive,
    not vanish from the pipeline entirely because extraction raised.
    """
    if not content:
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:  # noqa: BLE001 -- a bad PDF must not take down intake
        return ""
    return text[:MAX_CHARS]
