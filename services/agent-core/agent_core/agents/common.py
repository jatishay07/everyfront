"""Shared ADK runner plumbing for every named agent in the hierarchy.

This is the FORGE seed's `_ask`/`_run_once` pattern (services/agent-core/main.py),
extracted so all seven agents (Reader, Lookup, Clock, Auditor, Strategist,
Verifier, Filer) share one execution shape and one trace shape -- the
CLAUDE.md instruction for this work order is explicit: "Keep that serving
shape and that trace shape -- the trace is what the demo's activity feed is
made of." One copy also means the per-model-deadline / one-attempt-per-model
fix (docs/SPIKE.md, trap 4) cannot rot in six of seven copies while getting
fixed in the seventh.
"""

from __future__ import annotations

import asyncio

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

from .. import config

APP_NAME = "everyfront"


def build_agent(name: str, model: str, instruction: str, tools: list) -> Agent:
    return Agent(name=name, model=model, instruction=instruction, tools=tools)


async def run_agent_turn(name: str, model: str, instruction: str, tools: list, prompt: str) -> dict:
    """Run one agent for one turn, bounded so a busy model cannot hang the
    request, with a fallback model generation. Returns
    {"answer": str, "trace": [...], "model": str, "error": str | None}.

    `trace` entries are {"tool_call": ..., "args": ...} / {"tool_result": ...}
    -- the exact shape the seed's /ask endpoint proved out and that the events
    log surfaces to the UI activity feed.
    """
    try:
        async with asyncio.timeout(config.REQUEST_DEADLINE_S):
            return await _run_with_fallback(name, model, instruction, tools, prompt)
    except TimeoutError:
        return {
            "answer": "",
            "trace": [],
            "model": model,
            "error": f"deadline exceeded ({config.REQUEST_DEADLINE_S}s)",
        }


async def _run_with_fallback(
    name: str, model: str, instruction: str, tools: list, prompt: str
) -> dict:
    errors: list[str] = []
    for candidate_model in (model, config.GEMINI_FALLBACK_MODEL):
        for attempt in range(config.MAX_ATTEMPTS):
            try:
                async with asyncio.timeout(config.PER_MODEL_DEADLINE_S):
                    return await _run_once(name, candidate_model, instruction, tools, prompt)
            except TimeoutError:
                errors.append(f"{candidate_model} attempt {attempt + 1}: timed out")
            except Exception as exc:  # noqa: BLE001 -- record and try the next model
                errors.append(
                    f"{candidate_model} attempt {attempt + 1}: {type(exc).__name__}: {exc}"
                )
    return {"answer": "", "trace": [], "model": model, "error": "; ".join(errors)}


def make_fact_tool(name: str, doc: str, fact: dict):
    """Build a zero-argument tool that returns an already-computed fact.

    Every named agent in this hierarchy (Clock, Auditor, Lookup, ...) follows
    the playbook's "thin LLM wrapper" pattern literally: the CODE computes
    `fact` via a pure function (or, for Reader, a deterministic call into
    genai_client) *before* the agent ever runs, and the agent's only tool
    hands that exact value back. This removes the one failure mode a
    free-form-argument tool has -- the LLM mistranscribing a date or amount
    from prose into the tool call -- while still forcing the model to go
    through a real tool call for anything resembling computation, per §2.1.
    The LLM's job is solely to call the tool once and narrate the result in
    plain language for the events log.
    """

    def _tool() -> dict:
        return fact

    _tool.__name__ = name
    _tool.__doc__ = doc
    return _tool


async def _run_once(name: str, model: str, instruction: str, tools: list, prompt: str) -> dict:
    agent = build_agent(name, model, instruction, tools)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="pipeline")
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    trace: list[dict] = []
    answer = ""
    async for event in runner.run_async(
        user_id="pipeline", session_id=session.id, new_message=message
    ):
        for part in (event.content.parts if event.content else []) or []:
            if getattr(part, "function_call", None):
                trace.append(
                    {"tool_call": part.function_call.name, "args": dict(part.function_call.args)}
                )
            if getattr(part, "function_response", None):
                trace.append({"tool_result": part.function_response.response})
        if event.is_final_response() and event.content:
            answer = "".join(p.text or "" for p in event.content.parts).strip()

    return {"answer": answer, "trace": trace, "model": model, "error": None}
