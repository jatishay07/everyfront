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

import html as html_module
import io
import re

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


# `<script>`/`<style>` bodies are markup machinery, not prose -- dropped whole
# rather than de-tagged, or a Gmail HTML part's CSS block would arrive as a
# wall of selectors that an extraction model then has to read past.
_HTML_DROP_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_HTML_BREAK_RE = re.compile(r"(?i)<(br\s*/?|/p|/div|/tr|/li|/h[1-6])\s*>")
_HTML_TAG_RE = re.compile(r"(?s)<[^>]*>")
_BLANKS_RE = re.compile(r"[ \t\r\f\v]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """A Gmail `text/html` body part -> readable plain text.

    Deliberately a small regex de-tagger and NOT an HTML parser dependency:
    the only consumer is `gmail_client.extract_body_text`, which reaches for
    this ONLY when a message carries no `text/plain` alternative at all --
    every mainstream client (Gmail's own composer included) sends multipart/
    alternative with both. Getting a rare fallback approximately right is
    worth strictly less than one more package in a Cloud Run image.

    What it must get right, because a downstream model reads the result:
    block-level tags become line breaks (so "Household of three" does not run
    into the next sentence and become unquotable), entities are unescaped (so
    `&amp;` and `&nbsp;` do not survive into a verbatim quote), and script/
    style bodies are dropped entirely.
    """
    if not markup:
        return ""
    text = _HTML_DROP_RE.sub(" ", markup)
    text = _HTML_BREAK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = text.replace("\xa0", " ")
    text = _BLANKS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NEWLINES_RE.sub("\n\n", text).strip()[:MAX_CHARS]
