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

from .. import config, rules_bridge, statedfacts
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
    #
    # REMOVED 2026-08-26 (FORGE directive, persona 5 WO8): this used to also
    # run a local `_veto_charity_care_without_a_resolved_hospital` here, a
    # stopgap for a real bug in `rules.fronts._select_charity_care`
    # (unresolved hospital defaulted to nonprofit=True, ef-2026-0006) that
    # this persona could not edit directly. STATUTE has since fixed that bug
    # in `rules.fronts` itself (commit 69f4531) -- `select_fronts` now
    # already refuses charity_care correctly for an unresolved hospital, so
    # a second copy of that same check here would be exactly the kind of
    # duplicated, driftable logic §2.1 forbids ("all front-selection logic
    # lives in packages/rules"). Deleted rather than left dormant.
    # WHAT THE PATIENT SAID IS APPLIED HERE AND NOWHERE ELSE (persona 5, this
    # work order). `case["patient"]` is what documents and humans established;
    # `case["patient_stated"]` is what the patient claimed in the email their
    # bill was attached to. `statedfacts.decide_fronts` runs STATUTE's real,
    # unmodified `select_fronts` over a DERIVED patient view -- established
    # facts, plus a stated fact wherever the record is silent -- and reports
    # which stated facts each front's outcome actually turns on.
    #
    # The overlay lives for the length of this call and is never stored. That
    # is what keeps a claim and a fact distinguishable everywhere afterwards:
    # the case still records only what was established, and the fronts carry
    # `rests_on` naming exactly what they borrowed. `provisional` then gates
    # the filing (agents/verifier.py), the savings figure (pipeline), and the
    # §3.4 `charity_eligible` count (services/api).
    stated = case.get("patient_stated") or {}
    raw_decisions, rests_on = statedfacts.decide_fronts(rules_bridge.select_fronts, case, stated)
    decisions = _sequence(raw_decisions)

    fronts = []
    for d in decisions:
        borrowed = rests_on.get(d.front, ())
        fronts.append(
            {
                "front": d.front,
                "applicable": d.applicable,
                # The provenance prefix is prepended by CODE, ahead of
                # STATUTE's own sentence -- never phrased by a model and never
                # after the part that could be truncated. Same rule, same
                # reason, as `pipeline._SIMULATED_PREFIX`: whether a
                # determination rests on a document or on a sentence someone
                # typed is a fact about the world, not presentation.
                "reason": (
                    statedfacts.provisional_reason(d.reason, borrowed, stated)
                    if borrowed
                    else d.reason
                ),
                "citation": d.citation or "",
                "deadline": d.deadline.isoformat() if d.deadline is not None else None,
                "status": "open" if d.applicable else "na",
                # §3.1 additions (HANDOFF -> FORGE in this PR). Always present
                # and always the right type, so a consumer never has to tell
                # "not provisional" from "written before this existed" --
                # absent would be ambiguous exactly where ambiguity is
                # expensive.
                "provisional": bool(borrowed),
                "rests_on": list(borrowed),
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
