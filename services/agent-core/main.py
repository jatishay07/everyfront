"""Hello-world ADK agent on Cloud Run -- FORGE work order 1, gate (c).

This is the SEED for SWARM (persona 5). It exists to prove the runtime, not to
be the product: one agent, one tool, two endpoints. SWARM replaces the agent
hierarchy; the serving shape here should survive.

Why a plain Cloud Run service rather than a managed agent runtime: §6 names this
as the ADK-friction fallback, and it keeps the §1.3 requirement satisfied
(Cloud Run + a Google agent framework) with the fewest moving parts.

Demonstrates the §2.1 contract in miniature -- the LLM is not allowed to do date
arithmetic; it must call the tool, which is pure Python from packages/rules.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

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


@app.get("/healthz")
def healthz() -> dict:
    """Liveness only -- deliberately does NOT call the model.

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
