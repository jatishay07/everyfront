"""Test-only helpers, not collected by pytest (leading underscore)."""

from __future__ import annotations

from unittest.mock import patch

from agent_core import store as store_module
from agent_core.store import CaseStore


def make_memory_store() -> CaseStore:
    """A CaseStore guaranteed to use the in-memory backend, never touching a
    real Firestore project even if this machine has ADC configured (as the
    dev/CI environment for this work order does).

    Built through the REAL `CaseStore.__init__` with Firestore discovery
    stubbed out, rather than by a subclass that re-declares every in-memory
    field by hand. That subclass was a drift trap: adding a field to
    `CaseStore` -- or changing one's type, as the message-claim lease does to
    `_processed` -- left every test running against a store whose state did
    not match the real one, in a repo whose own HANDOFF says its worst defects
    all reported success while doing nothing.
    """
    with patch.object(store_module, "_try_firestore_client", return_value=None):
        return CaseStore()


def fake_turn(fact: dict, answer: str = "narrated") -> dict:
    """Shape-match an agents.*.run() coroutine's return value."""
    return {"fact": fact, "answer": answer, "trace": [], "model": "test-model", "error": None}


async def async_fake_turn(fact: dict, answer: str = "narrated"):
    return fake_turn(fact, answer)
