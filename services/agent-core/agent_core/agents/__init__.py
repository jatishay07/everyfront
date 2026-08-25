"""The ADK agent hierarchy: Reader, Lookup, Clock, Auditor, Strategist,
Verifier, Filer -- root agent Strategist with the other six wired up as
AgentTool sub-agents (see strategist.py), per BUILD_PLAYBOOK.md §4 persona 5.

Every module here follows one shape:
  1. a pure/deterministic "fact" is computed in Python first (a rules_bridge
     call, a genai_client call, a store read) -- §2.1: the code computes;
  2. an ADK Agent is given that fact behind a single zero-argument tool
     (agents.common.make_fact_tool) and asked to call it once and narrate;
  3. the caller (agent_core.pipeline) writes the fact to Firestore and
     appends one `cases/{id}/events` entry with the agent's name, action,
     the LLM's narration as `detail`, and the fact's citations.

Every agent's action is auditable this way, whether or not the LLM's prose
that turn was any good -- the audit log is built from the fact, not the
LLM's freeform text.
"""
