"""Environment configuration for agent-core.

Single place every module reads env from, so the four docs/SPIKE.md production
facts (Vertex `global` location, no API keys, Gemma thought-filtering, one
attempt per model generation) are set once and cannot drift between modules.
"""

from __future__ import annotations

import os

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
# SPIKE gate (c): Vertex serves Gemini 3.x ONLY from location=global. A regional
# default here would silently serve a pre-3.5 model and fail the §1.3 bar.
VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").upper() == "TRUE"

# §1.4, amended 2026-08-21 (gemma-3-27b-it is a 404).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")

# ADK already retries internally via tenacity (docs/SPIKE.md handoff to SWARM).
# One attempt per model generation; a per-model deadline stops a busy primary
# from eating the whole request budget and starving the fallback.
MAX_ATTEMPTS = int(os.environ.get("MODEL_MAX_ATTEMPTS", "1"))
REQUEST_DEADLINE_S = float(os.environ.get("REQUEST_DEADLINE_S", "120"))
PER_MODEL_DEADLINE_S = float(os.environ.get("PER_MODEL_DEADLINE_S", "40"))

# §3.2 topics this service publishes to. ATLAS provisions the actual topics;
# these names must match .env.example / the playbook exactly (see
# tests/test_contracts.py, the drift guard FORGE owns).
TOPIC_CASE_DOCUMENT_ADDED = os.environ.get("TOPIC_CASE_DOCUMENT_ADDED", "case.document.added")
TOPIC_CASE_ANALYSIS_COMPLETE = os.environ.get(
    "TOPIC_CASE_ANALYSIS_COMPLETE", "case.analysis.complete"
)
TOPIC_FILING_REQUESTED = os.environ.get("TOPIC_FILING_REQUESTED", "filing.requested")
TOPIC_FILING_COMPLETED = os.environ.get("TOPIC_FILING_COMPLETED", "filing.completed")

# Demo/day-to-day tolerance knobs, named here so they are not magic numbers
# buried in agents/verifier.py.
VERIFIER_INCOME_TOLERANCE_PCT = float(os.environ.get("VERIFIER_INCOME_TOLERANCE_PCT", "15"))

# Contract §3.1's `cases/{id}/documents/{doc_id}.gcs_uri` for
# `generated_application`/`generated_letter` docs (agent_core/document_storage.py,
# persona 5 WO6 task 2). `infra/setup.sh` names this bucket `ef-documents-<project>`
# but never actually wires `GCS_DOCUMENTS_BUCKET` into any service's deploy env
# (not even services/intake, which reads the same var) -- rather than block on
# an infra/ change outside this persona's owned paths, default to the exact
# name setup.sh already creates so this works the moment PROJECT_ID is set,
# same convention `packages/delivery/vendors/filing.py` (RELAY) already reads
# this same env var by.
GCS_DOCUMENTS_BUCKET = os.environ.get("GCS_DOCUMENTS_BUCKET") or (
    f"ef-documents-{PROJECT_ID}" if PROJECT_ID else ""
)
