"""Test-only helpers, not collected by pytest (leading underscore)."""

from __future__ import annotations

import threading

from agent_core.store import CaseStore


def make_memory_store() -> CaseStore:
    """A CaseStore guaranteed to use the in-memory backend, never touching a
    real Firestore project even if this machine has ADC configured (as the
    dev/CI environment for this work order does)."""

    class _NoFirestore(CaseStore):
        def __init__(self):
            self._client = None
            self._lock = threading.Lock()
            self._cases = {}
            self._documents = {}
            self._events = {}
            self._hospitals = {}
            self._filings = {}
            self._processed = set()

    return _NoFirestore()


def fake_turn(fact: dict, answer: str = "narrated") -> dict:
    """Shape-match an agents.*.run() coroutine's return value."""
    return {"fact": fact, "answer": answer, "trace": [], "model": "test-model", "error": None}


async def async_fake_turn(fact: dict, answer: str = "narrated"):
    return fake_turn(fact, answer)
