"""Calendar sync -- §4 persona 4 WO5.

No Google API client is required to exercise the pure logic (stable event
ids, red-if-due-soon coloring) or the "credentials not configured -> return
[] rather than crash" degradation path, so none of this needs
`google-api-python-client` installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from delivery.calendar_sync import (
    COLOR_ID_RED,
    _color_for,
    _days_remaining,
    _due_date,
    _explain_text,
    _field,
    _stable_event_id,
    sync_deadlines,
)


@dataclass(frozen=True)
class FakeDeadline:
    front: str
    name: str
    due: date | None
    citation: str = "26 CFR 1.501(r)-4(b)(1)(iv)"

    def explain(self, today: date | None = None) -> str:
        return f"{self.name}: due {self.due}"

    def days_remaining(self, today: date) -> int | None:
        return None if self.due is None else (self.due - today).days


TODAY = date(2026, 8, 25)


def test_stable_event_id_is_deterministic_and_charset_safe():
    d = FakeDeadline("ppdr", "Patient-provider dispute resolution", TODAY)
    id1 = _stable_event_id("case_1", d)
    id2 = _stable_event_id("case_1", d)
    assert id1 == id2  # same inputs -> same id -> sync_deadlines upserts, never duplicates
    assert id1.isalnum()
    assert all(ch in "0123456789abcdefghijklmnopqrstuv" for ch in id1)


def test_stable_event_id_differs_per_case_and_per_front():
    d = FakeDeadline("ppdr", "Patient-provider dispute resolution", TODAY)
    assert _stable_event_id("case_1", d) != _stable_event_id("case_2", d)
    other_front = FakeDeadline("charity_care", d.name, d.due)
    assert _stable_event_id("case_1", d) != _stable_event_id("case_1", other_front)


def _color(d, today):
    due = _due_date(d)
    return _color_for(due, _days_remaining(d, due, today))


def test_color_red_within_seven_days():
    soon = FakeDeadline("ppdr", "x", TODAY + timedelta(days=7))
    assert _color(soon, TODAY) == COLOR_ID_RED


def test_color_none_when_far_out():
    later = FakeDeadline("ppdr", "x", TODAY + timedelta(days=8))
    assert _color(later, TODAY) is None


def test_color_none_when_no_deadline():
    none_due = FakeDeadline("charity_care", "CA -- no deadline", None)
    assert _color(none_due, TODAY) is None


def test_color_red_when_already_overdue():
    overdue = FakeDeadline("ppdr", "x", TODAY - timedelta(days=3))
    assert _color(overdue, TODAY) == COLOR_ID_RED


# --- WO7: the serialized-dict shape agent_core.casedata.serialize_deadline
# actually produces, so agent-core can pass what it already has instead of
# a new shape both sides have to agree on (see calendar_sync.py's docstring).


def _serialized(front: str, name: str, due: date | None, citation: str, today: date) -> dict:
    explain = f"{name}: due {due}" if due else f"{name}: no deadline"
    return {
        "front": front,
        "name": name,
        "due": due.isoformat() if due else None,
        "citation": citation,
        "explain": explain,
    }


def test_field_due_date_and_explain_work_on_a_plain_dict():
    d = _serialized("ppdr", "x", TODAY + timedelta(days=3), "45 CFR 149.620", TODAY)
    assert _field(d, "front") == "ppdr"
    assert _due_date(d) == TODAY + timedelta(days=3)
    assert _explain_text(d, TODAY) == "x: due " + (TODAY + timedelta(days=3)).isoformat()
    assert _days_remaining(d, _due_date(d), TODAY) == 3


def test_due_date_dict_with_no_deadline_is_none():
    d = _serialized("charity_care", "CA -- no deadline", None, "26 CFR 1.501(r)-4", TODAY)
    assert _due_date(d) is None


def test_sync_deadlines_accepts_the_serialized_dict_shape(monkeypatch):
    """The exact shape agent_core.casedata.serialize_deadline produces --
    this is what closes the calendar HANDOFF without agent-core needing to
    keep the original Deadline objects around past Clock's tool call."""
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    deadlines = [
        _serialized("ppdr", "PPDR window", TODAY + timedelta(days=5), "45 CFR 149.620", TODAY)
    ]
    # No credentials configured -- still must not raise on the dict shape
    # before it ever gets to the point of needing credentials.
    assert sync_deadlines("case_1", "Maria G.", deadlines, today=TODAY) == []


def test_sync_deadlines_degrades_gracefully_without_credentials(monkeypatch):
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    deadlines = [FakeDeadline("ppdr", "x", TODAY)]
    # Must return [] rather than raise -- a missing calendar integration must
    # never fail the filing pipeline that called it.
    assert sync_deadlines("case_1", "Maria G.", deadlines, today=TODAY) == []


def test_sync_deadlines_skips_deadlines_with_no_due_date(monkeypatch):
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    deadlines = [FakeDeadline("charity_care", "CA -- no deadline", None)]
    assert sync_deadlines("case_1", "Maria G.", deadlines, today=TODAY) == []
