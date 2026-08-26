"""agent-core: the ADK agent hierarchy + Pub/Sub push subscriber.

The bottom of this file is FORGE's original hello-world seed (gate (c)) --
kept byte-for-byte in its serving shape and trace shape per this work order's
instructions, since it is a deployed, verified reference for what a correct
ADK tool-call trace looks like. Everything above `# --- seed`  is SWARM's
persona-5 work: the real Reader/Lookup/Clock/Auditor/Strategist/Verifier/Filer
hierarchy in `agent_core/`, wired up here as a Pub/Sub push subscriber plus a
couple of internal endpoints `services/api` calls synchronously.

Why both a push subscriber AND a synchronous internal call for the same
events (see /pubsub/document-added vs /internal/process_document): this repo
has no upstream intake service yet (services/intake is still RELAY's empty
stub) so nothing publishes `case.document.added` except this codebase's own
`/demo/inject_bill`. That endpoint calls the synchronous internal route so a
live demo recording does not depend on push-delivery latency, while ALSO
publishing the real Pub/Sub message so the topic is genuinely exercised (and
visible as evidence in the Cloud Console) for architecture purposes. Once
RELAY's Gmail intake ships and publishes independently, the push-subscriber
path is what actually matters and the synchronous route is fine to keep as a
test/ops escape hatch.

Idempotency (contract §2.3): both push endpoints dedupe on the Pub/Sub
message id via `CaseStore.has_processed_message` / `mark_message_processed`
before doing any work.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import date, timedelta

from agent_core import pipeline
from agent_core.store import store
from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

logger = logging.getLogger("agent_core.main")

# --- seed (FORGE gate (c)) -- serving shape + trace shape preserved verbatim ---

APP_NAME = "everyfront"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")

# Gate (c) findings: the Gemini free tier returns 503 "high demand" intermittently
# and succeeds on retry. 503 means BUSY, never "model missing". See docs/SPIKE.md.
#
# ADK already retries internally via tenacity, so DO NOT stack a deep retry loop
# on top -- 3 attempts x 2 models x ADK's own backoff blew past 180s locally and
# would exceed the Cloud Run request timeout. One attempt per model generation is
# the right shape: ADK absorbs transient blips, we only fall back a generation.
MAX_ATTEMPTS = int(os.environ.get("MODEL_MAX_ATTEMPTS", "1"))
# Hard ceiling on the whole handler, kept well under deploy.sh's --timeout=300.
REQUEST_DEADLINE_S = float(os.environ.get("REQUEST_DEADLINE_S", "120"))
# Per-model ceiling. Without this the primary model's internal retries eat the
# ENTIRE request budget and the fallback never runs -- measured locally: a busy
# gemini-3.7-flash burned all 120s on its own and 3.5 was never attempted.
# Falling back late is useless; falling back is the whole point.
PER_MODEL_DEADLINE_S = float(os.environ.get("PER_MODEL_DEADLINE_S", "40"))


def compute_fap_deadline(first_statement_date: str) -> dict:
    """Federal charity-care application deadline for a bill.

    The 240-day window runs from the first POST-DISCHARGE billing statement,
    not the date of service (26 CFR 1.501(r)-4(b)(1)(iv)).

    Args:
        first_statement_date: ISO-8601 date of the first post-discharge statement.
    """
    try:
        basis = date.fromisoformat(first_statement_date)
    except ValueError:
        return {"error": f"not an ISO date: {first_statement_date!r}"}
    return {
        "due": (basis + timedelta(days=240)).isoformat(),
        "basis_date": basis.isoformat(),
        "citation": "26 CFR 1.501(r)-4(b)(1)(iv)",
    }


INSTRUCTION = (
    "You are a medical-bill advocate. You must never compute a date yourself -- "
    "always call a tool for date arithmetic, and repeat the citation the tool "
    "returns. If a tool returns an error, say so plainly."
)


def build_agent(model: str) -> Agent:
    return Agent(
        name="everyfront_seed",
        model=model,
        instruction=INSTRUCTION,
        tools=[compute_fap_deadline],
    )


app = FastAPI(title="Every Front agent-core")


class Ask(BaseModel):
    question: str = "The first post-discharge statement was 2026-03-01. When is the deadline?"


@app.get("/")
def root() -> dict:
    return {"service": "agent-core", "model": MODEL, "status": "ok"}


@app.get("/health")
def healthz() -> dict:
    """Liveness only -- deliberately does NOT call the model.

    Named /health, not /healthz: the Cloud Run frontend returns its own 404 for
    /healthz before the request reaches the container, even though FastAPI
    registers the route (it shows up in /openapi.json). Verified on the deployed
    service, 2026-08-25.

    A health check that hits Gemini would flap on the 503s above and, worse,
    bill us for every probe.
    """
    return {"ok": True}


@app.post("/ask")
async def ask(req: Ask) -> dict:
    """Run the seed agent, bounded so a busy model cannot hang the request."""
    try:
        async with asyncio.timeout(REQUEST_DEADLINE_S):
            return await _ask(req)
    except TimeoutError:
        return {"error": "deadline exceeded", "seconds": REQUEST_DEADLINE_S}


async def _ask(req: Ask) -> dict:
    errors: list[str] = []
    for model in (MODEL, FALLBACK_MODEL):
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with asyncio.timeout(PER_MODEL_DEADLINE_S):
                    return await _run_once(model, req.question, attempt)
            except TimeoutError:
                errors.append(
                    f"{model} attempt {attempt + 1}: timed out after {PER_MODEL_DEADLINE_S}s"
                )
            except Exception as exc:  # noqa: BLE001 -- record and try the next model
                errors.append(f"{model} attempt {attempt + 1}: {type(exc).__name__}")
    return {"error": "all models exhausted", "detail": errors}


async def _run_once(model: str, question: str, attempt: int) -> dict:
    """One agent turn against one model. Raises on failure; caller decides."""
    runner = InMemoryRunner(agent=build_agent(model), app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="anonymous")
    message = types.Content(role="user", parts=[types.Part(text=question)])

    trace: list[dict] = []
    answer = ""
    async for event in runner.run_async(
        user_id="anonymous", session_id=session.id, new_message=message
    ):
        for part in (event.content.parts if event.content else []) or []:
            if getattr(part, "function_call", None):
                trace.append(
                    {
                        "tool_call": part.function_call.name,
                        "args": dict(part.function_call.args),
                    }
                )
            if getattr(part, "function_response", None):
                trace.append({"tool_result": part.function_response.response})
        if event.is_final_response() and event.content:
            answer = "".join(p.text or "" for p in event.content.parts).strip()

    # The trace is the point, not decoration: §2.1 says the code computes and the
    # LLM narrates, and this is the evidence that it actually happened that way.
    return {"model": model, "attempt": attempt + 1, "answer": answer, "trace": trace}


# --- SWARM persona 5: the real agent hierarchy ------------------------------


class ProcessDocument(BaseModel):
    case_id: str
    doc_id: str


class ProcessDocuments(BaseModel):
    case_id: str
    doc_ids: list[str]


class ApproveFiling(BaseModel):
    case_id: str
    front: str


@app.post("/internal/process_document")
async def process_document(req: ProcessDocument) -> dict:
    """Synchronous entry point for `case.document.added` -- see module
    docstring for why this exists alongside the push-subscriber route below.
    """
    return await pipeline.on_document_added(req.case_id, req.doc_id)


@app.post("/internal/process_documents")
async def process_documents(req: ProcessDocuments) -> dict:
    """Batch synchronous entry point: run Reader for every doc_id CONCURRENTLY
    and run the Lookup/Clock/Auditor/Strategist cascade exactly ONCE (defect
    #3, persona 5 WO2) -- for a caller that already has every document for a
    case up front (`services/api`'s `/demo/inject_bill`), instead of N calls
    to `/internal/process_document` each re-running the whole cascade. See
    `agent_core.pipeline.process_case_documents`'s docstring.
    """
    return await pipeline.process_case_documents(req.case_id, req.doc_ids)


@app.post("/internal/approve_filing")
async def approve_filing(req: ApproveFiling) -> dict:
    """Synchronous entry point services/api's `POST /cases/{id}/approve_filing`
    calls after recording the human's approval. Runs Verifier; on pass,
    publishes `filing.requested` and runs Filer. Never files without this
    call having happened first -- see agent_core/pipeline.py.
    """
    return await pipeline.approve_and_request_filing(req.case_id, req.front)


def _decode_push_envelope(body: dict) -> tuple[str, dict]:
    """Pub/Sub push envelope -> (message_id, json payload)."""
    message = body.get("message") or {}
    message_id = message.get("messageId") or message.get("message_id") or str(uuid.uuid4())
    data_b64 = message.get("data", "")
    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8")) if data_b64 else {}
    except (ValueError, json.JSONDecodeError):
        payload = {}
    return message_id, payload


@app.post("/pubsub/document-added")
async def pubsub_document_added(body: dict) -> dict:
    """Push endpoint for the `ef-document-added` subscription (topic
    `case.document.added`, contract §3.2). Idempotent on redelivery (§2.3).
    """
    message_id, payload = _decode_push_envelope(body)
    if store.has_processed_message(message_id):
        return {"status": "duplicate, skipped", "message_id": message_id}
    case_id, doc_id = payload.get("case_id"), payload.get("doc_id")
    if not case_id or not doc_id:
        logger.warning("malformed case.document.added push: %s", payload)
        return {"status": "malformed payload, acked to avoid redelivery storm"}
    result = await pipeline.on_document_added(case_id, doc_id)
    store.mark_message_processed(message_id)
    return {"status": "ok", "result_keys": list(result.keys())}


@app.post("/pubsub/filing-requested")
async def pubsub_filing_requested(body: dict) -> dict:
    """Push endpoint for the `ef-filing-requested` subscription (topic
    `filing.requested`, contract §3.2). Idempotent on redelivery (§2.3).

    CHANGED 2026-08-25 (SWARM WO7, "approval times out clients"): this is now
    the ONLY place Filer actually runs for a human-approved filing --
    `pipeline.approve_and_request_filing` publishes `filing.requested` and
    returns immediately rather than running Filer in-process (see that
    function's docstring for why: a synchronous Filer call inside the HTTP
    request routinely took over 6 minutes and blew past every timeout in the
    chain). That only works now because `infra/deploy.sh` actually wires
    `ef-filing-requested` to this push endpoint with an OIDC service account
    -- before that fix it sat as a PULL subscription with no subscriber, so
    this route was reachable only by hand.

    `pipeline.finalize_filing` runs Filer and, on failure, reverts the front
    to "open" (persona 5 WO6 task 1) so a failed delivery does not leave the
    case wedged -- then re-raises, so a genuine failure surfaces as a non-2xx
    response and Pub/Sub's own redelivery/backoff retries it, rather than
    this route silently acking a filing that never happened.
    """
    message_id, payload = _decode_push_envelope(body)
    if store.has_processed_message(message_id):
        return {"status": "duplicate, skipped", "message_id": message_id}
    case_id, front, filing_id = (
        payload.get("case_id"),
        payload.get("front"),
        payload.get("filing_id"),
    )
    if not case_id or not front or not filing_id:
        logger.warning("malformed filing.requested push: %s", payload)
        return {"status": "malformed payload, acked to avoid redelivery storm"}
    case = store.get_case(case_id)
    already_filed = case is not None and any(
        f.get("front") == front and f.get("status") == "filed" for f in case.get("fronts") or []
    )
    if not already_filed:
        await pipeline.finalize_filing(case_id, front, filing_id)
    store.mark_message_processed(message_id)
    return {"status": "ok"}
