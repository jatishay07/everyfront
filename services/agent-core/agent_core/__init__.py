"""The real agent-core package: Firestore store, rules/delivery bridges,
genai client, and the ADK agent hierarchy (agent_core.agents) that
services/agent-core/main.py wires up as a Pub/Sub push subscriber.

See BUILD_PLAYBOOK.md §4 persona 5 (SWARM) for the work order, and
agent_core.agents's docstring for how each named agent follows the
"code computes, LLM narrates" pattern (§2.1).
"""
