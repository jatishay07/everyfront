"""Google Calendar sync -- §4 persona 4 WO5.

"write every `Deadline` to the demo Google Calendar with color coding (red
<=7d) and the citation in the description."

Takes `rules.deadlines.Deadline` objects (or any object with the same
`.due`/`.name`/`.front`/`.citation`/`.explain()`/`.days_remaining()` shape --
typed loosely on purpose so a test can pass a plain stand-in without
importing `packages/rules`) -- OR the serialized dict shape
`agent_core.casedata.serialize_deadline` actually produces and is the only
form agent-core keeps around after Clock's tool call (`due` becomes an ISO
string, `.explain()` becomes a precomputed `explain` string field rather than
a method). WO7: accepting both here on purpose, so wiring this into
agent-core is "pass whatever you already have" rather than a new shape both
sides have to agree on and get right on the first try -- which is exactly
how `services/agent-core/agent_core/delivery_bridge.py` silently fell
through to a simulated vendor for weeks (see that file's WO7 rewrite
history: two correct halves, a seam that never joined). See `_field`/
`_due_date`/`_explain_text`/`_days_remaining` below for the normalization.

Agreement §2.1 still holds here: this module renders a Deadline that
STATUTE's pure functions already computed; it does not decide what a
deadline is.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import date
from typing import Any, Protocol

from .google_auth import MissingCredentialsError, load_user_credentials

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Google's documented "Tomato" palette color -- a real red, not a CSS guess.
# https://developers.google.com/calendar/api/v3/reference/colors
COLOR_ID_RED = "11"
DUE_SOON_DAYS = 7


class DeadlineLike(Protocol):
    front: str
    name: str
    due: date | None
    citation: str

    def explain(self, today: date | None = None) -> str: ...

    def days_remaining(self, today: date) -> int | None: ...


def _field(d: Any, name: str) -> Any:
    return d.get(name) if isinstance(d, dict) else getattr(d, name)


def _due_date(d: Any) -> date | None:
    due = _field(d, "due")
    return date.fromisoformat(due) if isinstance(due, str) else due


def _explain_text(d: Any, today: date) -> str:
    """A dict already carries the precomputed string (see module docstring);
    an object needs its `.explain()` method called."""
    if isinstance(d, dict) and isinstance(d.get("explain"), str):
        return d["explain"]
    return d.explain(today)


def _days_remaining(d: Any, due: date | None, today: date) -> int | None:
    if due is None:
        return None
    if isinstance(d, dict):
        return (due - today).days
    return d.days_remaining(today)


def _stable_event_id(case_id: str, d: Any) -> str:
    """Deterministic Calendar event id so re-running sync UPSERTS instead of
    duplicating events on redelivery (agreement §2.3 applies to this sync
    the same way it applies to a Pub/Sub handler).

    Calendar event ids must be base32hex: lowercase a-v and digits 0-9.
    """
    raw = f"{case_id}:{_field(d, 'front')}:{_field(d, 'name')}".encode()
    digest = hashlib.sha1(raw).digest()  # noqa: S324 -- id derivation, not security
    return base64.b32hexencode(digest).decode().lower().rstrip("=")


def _color_for(due: date | None, days_left: int | None) -> str | None:
    if due is None or days_left is None:
        return None
    return COLOR_ID_RED if days_left <= DUE_SOON_DAYS else None


def sync_deadlines(
    case_id: str,
    patient_label: str,
    deadlines: list[DeadlineLike] | list[dict[str, Any]],
    *,
    calendar_id: str | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Upsert one all-day Calendar event per deadline that HAS a due date.

    Deadlines with `due is None` (e.g. CA's no-deadline charity-care rule)
    are skipped -- there is nothing to put on a calendar, and creating a
    fake "no deadline" event would be exactly the kind of invented date
    `rules/deadlines.py` goes out of its way to avoid.

    Returns the list of events written, or `[]` with nothing raised if the
    demo account's OAuth credentials are not configured -- see
    `google_auth.MissingCredentialsError`. A missing calendar sync must never
    fail the filing that triggered it.
    """
    today = today or date.today()
    calendar_id = calendar_id or os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    try:
        creds = load_user_credentials(CALENDAR_SCOPES)
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except MissingCredentialsError:
        return []

    service = build("calendar", "v3", credentials=creds)
    out: list[dict[str, Any]] = []
    for d in deadlines:
        due = _due_date(d)
        if due is None:
            continue
        front = _field(d, "front")
        name = _field(d, "name")
        citation = _field(d, "citation")
        days_left = _days_remaining(d, due, today)
        event_id = _stable_event_id(case_id, d)
        body = {
            "id": event_id,
            "summary": f"[{front.upper()}] {name} -- {patient_label}",
            "description": f"{_explain_text(d, today)}\n\nCitation: {citation}",
            "start": {"date": due.isoformat()},
            "end": {"date": due.isoformat()},
        }
        color = _color_for(due, days_left)
        if color:
            body["colorId"] = color
        try:
            event = (
                service.events()
                .update(calendarId=calendar_id, eventId=event_id, body=body)
                .execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                event = service.events().insert(calendarId=calendar_id, body=body).execute()
            else:
                raise
        out.append({"front": front, "name": name, "event_id": event["id"], "due": due.isoformat()})
    return out
