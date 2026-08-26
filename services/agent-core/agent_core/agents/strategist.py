"""Strategist: the root agent of the hierarchy.

Playbook §4 persona 5, WO1: "consumes `select_fronts`, sequences actions
(validation first when in collections), writes the plan to `fronts[]`, emits
`filing.requested` per front -- but only after `POST
/cases/{id}/approve_filing`."

Architecturally this is the ADK root agent: `build_root_agent()` wires up the
other six named agents as `AgentTool` sub-agents, matching "root agent
Strategist with sub-agents as tools" literally. In production the actual
per-event pipeline (agent_core.pipeline) drives each named agent directly in a
fixed, deterministic order rather than letting the root LLM freely decide
which sub-agent to call and when -- the human-in-the-loop filing gate is a
hazard the code enforces (`pipeline.approve_and_request_filing` will not run
Filer without a prior approve_filing call), not something left to model
discretion. `build_root_agent()` still exists and is exercised by
`main.py`'s `/ask`-style debug endpoint, so the hierarchy is real and
inspectable, not just a docstring.
"""

from __future__ import annotations

from google.adk.tools.agent_tool import AgentTool

from .. import config, rules_bridge
from . import auditor, clock, common, filer, lookup, reader, verifier

NAME = "strategist"

INSTRUCTION = (
    "You are Strategist, the lead agent for a medical-bill advocacy caseload. You "
    "coordinate Reader, Lookup, Clock, Auditor, Verifier, and Filer. Call "
    "get_strategist_result exactly once to see the selected legal fronts and their "
    "deadlines, then summarize the plan in 2-4 sentences, naming which front (if "
    "any) is most urgent. You must NEVER instruct Filer to send anything -- filing "
    "requires a human's approve_filing call first, which happens outside this "
    "conversation."
)


def build_root_agent(model: str | None = None):
    """The literal 'root agent Strategist with sub-agents as tools' hierarchy."""
    model = model or config.GEMINI_MODEL
    # AgentTool wants a constructed Agent instance per sub-agent; build one
    # each from the plain instruction (no per-case tools bound at hierarchy-
    # build time -- those are attached per-turn by each agent's own run()).
    tools = [
        AgentTool(agent=common.build_agent("reader", model, reader.INSTRUCTION, [])),
        AgentTool(agent=common.build_agent("lookup", model, lookup.INSTRUCTION, [])),
        AgentTool(agent=common.build_agent("clock", model, clock.INSTRUCTION, [])),
        AgentTool(agent=common.build_agent("auditor", model, auditor.INSTRUCTION, [])),
        AgentTool(agent=common.build_agent("verifier", model, verifier.INSTRUCTION, [])),
        AgentTool(agent=common.build_agent("filer", model, filer.INSTRUCTION, [])),
    ]
    return common.build_agent(NAME, model, INSTRUCTION, tools)


def _sequence(decisions: list) -> list:
    """Validation-first ordering, applied defensively on top of whatever
    `select_fronts` returns. STATUTE's real `rules.fronts.select_fronts`
    already reorders around an applicable `debt_validation` (see that
    module's docstring), so this is a no-op there; it only matters for the
    fallback path in `rules_bridge`, which does not guarantee order itself.
    """

    def key(d):
        return (0 if (d.front == "debt_validation" and d.applicable) else 1, d.front)

    return sorted(decisions, key=key)


def _facts(case_id: str, case: dict) -> dict:
    # select_fronts (rules.fronts) already computes each front's OWN deadline
    # (from compute_deadlines) and, for charity_care, bakes the eligibility
    # screen's explanation straight into `.reason` -- there is nothing left
    # for the Strategist to recompute here; it is pure orchestration.
    decisions = _sequence(rules_bridge.select_fronts(case))

    fronts = []
    for d in decisions:
        fronts.append(
            {
                "front": d.front,
                "applicable": d.applicable,
                "reason": d.reason,
                "citation": d.citation or "",
                "deadline": d.deadline.isoformat() if d.deadline is not None else None,
                "status": "open" if d.applicable else "na",
            }
        )

    return {
        "case_id": case_id,
        "fronts": fronts,
        "source": rules_bridge.bridge_sources()["select_fronts"],
    }


async def run(case_id: str, case: dict) -> dict:
    fact = _facts(case_id, case)
    tool = common.make_fact_tool(
        "get_strategist_result",
        "Return the selected legal fronts, in filing order, with their deadlines.",
        fact,
    )
    # No raw case_id in the prompt -- see reader.py's docstring note (bug
    # found live 2026-08-25): an LLM's freeform narration lands verbatim in
    # `events/{id}.detail`, which a case rename cannot scrub after the fact.
    prompt = "Build the strategy for this case. Call get_strategist_result first."
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
