"""Direct google-genai calls for the two things ADK's Agent/tool loop is the
wrong shape for: Gemma's cheap first-pass classification, and Gemini's
temperature-0 structured extraction. (The narration agents in agent_core.agents
use ADK's Agent + InMemoryRunner instead, matching the seed's serving shape.)

Every production fact from docs/SPIKE.md gate (c) lives here:

  * Gemma 4 returns TWO parts, one with `thought: true`. Concatenating both
    yields a restatement of the prompt instead of a label -- filtered out in
    `_answer_text`. This took accuracy from 0/5 to 5/5 with no prompt change.
  * `thinkingConfig.thinkingBudget: 0` is REJECTED (HTTP 400) for Gemma 4 --
    thinking cannot be disabled, only its output filtered after the fact.
  * Gemini free tier returned one transient 503 "high demand" in the spike and
    succeeded on retry -- 503 means busy, never "model missing". One retry
    with backoff is implemented; it is NOT the deep retry loop docs/SPIKE.md
    warns against stacking on top of ADK's own tenacity retries, because this
    module calls google-genai directly and has no ADK retry underneath it.
"""

from __future__ import annotations

import json
import time
from typing import Any

from google import genai
from google.genai import types

from . import config

_client: genai.Client | None = None


def _client_singleton() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=config.USE_VERTEX,
            project=config.PROJECT_ID or None,
            location=config.VERTEX_LOCATION,
        )
    return _client


def _answer_text(resp: Any) -> str:
    """Filter Gemma 4's `thought: true` parts. See module docstring, trap 1."""
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        return (getattr(resp, "text", None) or "").strip()
    parts = candidates[0].content.parts or []
    return "".join((p.text or "") for p in parts if not getattr(p, "thought", False)).strip()


def _generate_with_retry(model: str, contents, config_obj, *, retries: int = 1):
    """One retry on failure -- gate (c) measured a transient 503 succeed on
    retry #1. More than one retry here would stack with the caller's own
    per-model deadline and risk the same timeout trap docs/SPIKE.md warns
    about, so this stays a single, short retry.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _client_singleton().models.generate_content(
                model=model, contents=contents, config=config_obj
            )
        except Exception as exc:  # noqa: BLE001 -- retried once, then re-raised
            last_exc = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# Contract §3.1 documents.type enum -- Gemma's first-pass classification job.
DOCUMENT_TYPES = (
    "bill",
    "itemized_bill",
    "denial_letter",
    "collection_notice",
    "gfe",
    "income_proof",
)


def _classify_prompt(text: str) -> str:
    return (
        "Classify this medical-billing document into exactly one label from: "
        + ", ".join(DOCUMENT_TYPES)
        + ". Respond with ONLY the label, nothing else.\n\n---\n"
        + text[:8000]
    )


def gemma_classify(text: str, model: str | None = None) -> dict[str, Any]:
    """First-pass document classification -- the bonus-point Gemma model.

    Returns {"label": one of DOCUMENT_TYPES or "unknown", "raw": answer text,
    "model": model, "error": str | None, "fallback_model_used": bool}. Never
    raises -- a classifier that can crash the Reader agent's whole turn is
    worse than one that returns "unknown" and lets Gemini's structured
    extraction (or a human) take over.

    HANDOFF -> FORGE/ATLAS, discovered running this work order (not in
    docs/SPIKE.md, which only verified Gemma against the AI Studio Gemini
    Developer API with a free API key -- a different serving surface):
    `gemma-4-26b-a4b-it` returns HTTP 404 "not found or your project does not
    have access to it" when called via Vertex AI (`vertexai=True`) in project
    everyfront-hack-2026, tried in both `global` and `us-central1`. Gemini 3.7
    Flash IS reachable via Vertex `global`, confirmed live in this same run.
    Two possible causes, neither fixable from this service's code: (1) Gemma
    is not offered as a Vertex shared "publisher model" endpoint at all and
    needs a Model Garden self-deploy (a provisioned, billed GPU endpoint) --
    persona 5 has no mandate or budget authority to provision that; or (2) the
    project needs to accept Model Garden terms for Gemma in the console first.
    Putting a Gemini Developer API key in the container to reach Gemma via
    generativelanguage.googleapis.com would satisfy the bonus-point ask but
    directly violates this work order's "NEVER put an API key in the
    container" instruction, so this function does NOT do that. Instead it
    tries Gemma via Vertex first (so the bonus point is claimed automatically
    the moment ATLAS/FORGE resolve access) and falls back to a temperature-0
    Gemini call for the same classification prompt, clearly flagged via
    `fallback_model_used`, so Reader keeps working end-to-end either way.
    """
    model = model or config.GEMMA_MODEL
    prompt = _classify_prompt(text)
    try:
        # Trap 2: thinkingConfig.thinkingBudget=0 is HTTP 400 on Gemma 4 -- do
        # not attempt to disable thinking, only filter its output.
        resp = _generate_with_retry(model, prompt, types.GenerateContentConfig(temperature=0.0))
    except Exception as exc:  # noqa: BLE001 -- fall back to Gemini, see docstring
        return _classify_with_fallback(prompt, model, str(exc))

    raw = _answer_text(resp)
    label = raw.strip().lower().strip(".")
    if label not in DOCUMENT_TYPES:
        # Gemma sometimes wraps the label in a short sentence despite the
        # instruction; take the first matching token rather than failing.
        label = next((d for d in DOCUMENT_TYPES if d in raw.lower()), "unknown")
    return {
        "label": label,
        "raw": raw,
        "model": model,
        "error": None,
        "fallback_model_used": False,
    }


def _classify_with_fallback(prompt: str, gemma_model: str, gemma_error: str) -> dict[str, Any]:
    try:
        resp = _generate_with_retry(
            config.GEMINI_MODEL, prompt, types.GenerateContentConfig(temperature=0.0)
        )
    except Exception as exc:  # noqa: BLE001 -- both models failed
        return {
            "label": "unknown",
            "raw": "",
            "model": gemma_model,
            "error": f"gemma: {gemma_error}; gemini fallback: {exc}",
            "fallback_model_used": True,
        }
    raw = _answer_text(resp)
    label = raw.strip().lower().strip(".")
    if label not in DOCUMENT_TYPES:
        label = next((d for d in DOCUMENT_TYPES if d in raw.lower()), "unknown")
    return {
        "label": label,
        "raw": raw,
        "model": f"{config.GEMINI_MODEL} (gemma unavailable: {gemma_error})",
        "error": None,
        "fallback_model_used": True,
    }


def gemini_extract_json(
    text: str, schema: dict, instruction: str, model: str | None = None
) -> dict[str, Any]:
    """Temperature-0 structured extraction into a JSON schema, retry-on-invalid.

    `schema` is a JSON-schema dict (response_schema). Returns the parsed dict
    on success, or {"_extraction_error": str} on failure after retry -- the
    Reader agent must treat that as "needs human review", never as a silent
    empty extraction.
    """
    model = model or config.GEMINI_MODEL
    cfg = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=schema,
    )
    prompt = f"{instruction}\n\nDocument text:\n---\n{text[:12000]}"

    for attempt in range(2):  # one retry on invalid JSON, per §1.4 SWARM WO1
        try:
            resp = _generate_with_retry(model, prompt, cfg)
        except Exception as exc:  # noqa: BLE001
            if attempt == 1:
                return {"_extraction_error": str(exc)}
            continue
        raw = _answer_text(resp) or (getattr(resp, "text", None) or "")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            if attempt == 1:
                return {"_extraction_error": f"invalid JSON after retry: {raw[:500]!r}"}
            continue
    return {"_extraction_error": "unreachable"}
