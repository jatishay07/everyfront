"""Test-only helpers, not collected by pytest (leading underscore)."""

from __future__ import annotations

import threading

from api_core.store import CaseStore


def make_memory_store() -> CaseStore:
    class _NoFirestore(CaseStore):
        def __init__(self):
            self._client = None
            self._lock = threading.Lock()
            self._cases = {}
            self._documents = {}
            self._events = {}
            self._hospitals = {}
            self._filings = {}

    return _NoFirestore()
