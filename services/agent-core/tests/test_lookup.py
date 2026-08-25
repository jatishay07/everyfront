"""agent_core.agents.lookup -- defect #2: resolve by provider name too, not
just EIN. A real bill almost never prints an EIN; LEDGER's 200-hospital seed
is keyed by EIN but every record carries a real `name`.
"""

from __future__ import annotations

import asyncio

from _helpers import make_memory_store
from agent_core.agents import lookup


def _fresh_directory(monkeypatch, s):
    """Each test gets its own store AND its own cache -- the module-level
    `_directory` cache must not leak seeded hospitals between tests."""
    monkeypatch.setattr(lookup, "store", s)
    monkeypatch.setattr(lookup, "_directory", lookup._HospitalDirectory())


def test_resolves_by_ein_when_present(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("94-0562680", {"name": "Sutter Bay Hospitals", "nonprofit": True})

    case = {"bill": {"hospital_ein": "94-0562680", "provider_name": "Sutter Bay Hospitals"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["method"] == "ein"
    assert fact["ein"] == "94-0562680"


def test_resolves_by_provider_name_when_no_ein_on_bill(monkeypatch):
    """The realistic case: a real bill prints the hospital's name, not its
    EIN, so Reader's extraction leaves `hospital_ein` blank."""
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("36-2169147", {"name": "Advocate Christ Medical Center", "nonprofit": True})

    case = {"bill": {"hospital_ein": "", "provider_name": "Advocate Christ Medical Center"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["method"] == "provider_name"
    assert fact["ein"] == "36-2169147"
    assert fact["hospital"]["name"] == "Advocate Christ Medical Center"


def test_resolves_by_provider_name_case_and_punctuation_insensitive(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("94-6174066", {"name": "Stanford Health Care", "nonprofit": True})

    case = {"bill": {"provider_name": "STANFORD HEALTH CARE (SYNTHETIC demo data)"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["ein"] == "94-6174066"


def test_ein_wins_over_name_when_both_present_and_ein_resolves(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("36-2169147", {"name": "Advocate Christ Medical Center", "nonprofit": True})

    case = {"bill": {"hospital_ein": "36-2169147", "provider_name": "Some Unrelated Name Entirely"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["method"] == "ein"


def test_falls_back_to_name_when_ein_on_bill_does_not_resolve(monkeypatch):
    """A wrong/stale EIN on the bill should not block an honest name match."""
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("36-2169147", {"name": "Advocate Christ Medical Center", "nonprofit": True})

    case = {
        "bill": {"hospital_ein": "99-9999999", "provider_name": "Advocate Christ Medical Center"}
    }
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["method"] == "provider_name"
    assert fact["ein"] == "36-2169147"


def test_unresolved_is_honest_not_a_guess(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("36-2169147", {"name": "Advocate Christ Medical Center", "nonprofit": True})

    case = {"bill": {"provider_name": "Completely Different Regional Medical Center"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is False
    assert fact["hospital"] is None


def test_no_ein_or_name_is_honest():
    case = {"bill": {}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is False
    assert "neither" in fact["note"]


def test_for_profit_name_match_gives_the_honest_no_501r_note(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("00-0000001", {"name": "Prairie Crossing Medical Center", "nonprofit": False})

    case = {"bill": {"provider_name": "Prairie Crossing Medical Center"}}
    fact = lookup._resolve_fact(case)
    assert fact["resolved"] is True
    assert fact["nonprofit"] is False
    assert "FOR-PROFIT" in fact["note"]


def test_run_returns_fact_and_narration(monkeypatch):
    s = make_memory_store()
    _fresh_directory(monkeypatch, s)
    s.put_hospital("94-0562680", {"name": "Sutter Bay Hospitals", "nonprofit": True})
    case = {"bill": {"provider_name": "Sutter Bay Hospitals"}}

    turn = asyncio.run(lookup.run("c1", case))
    assert turn["fact"]["resolved"] is True
    assert "answer" in turn
