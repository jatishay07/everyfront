"""Google Calendar sync -- §4 persona 4 WO5.

"write every `Deadline` to the demo Google Calendar with color coding (red
<=7d) and the citation in the description."

Takes `rules.deadlines.Deadline` objects directly (or any object with the
same `.due` / `.name` / `.front` / `.citation` / `.explain()` shape -- typed
loosely on purpose so a test can pass a plain stand-in without importing
`packages/rules`). Agreement §2.1 still holds here: this module renders a
Deadline that STATUTE's pure functions already computed; it does not decide
what a deadline is.
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


def _stable_event_id(case_id: str, deadline: DeadlineLike) -> str:
    """Deterministic Calendar event id so re-running sync UPSERTS instead of
    duplicating events on redelivery (agreement §2.3 applies to this sync
    the same way it applies to a Pub/Sub handler).

    Calendar event ids must be base32hex: lowercase a-v and digits 0-9.
    """
    raw = f"{case_id}:{deadline.front}:{deadline.name}".encode()
    digest = hashlib.sha1(raw).digest()  # noqa: S324 -- id derivation, not security
    return base64.b32hexencode(digest).decode().lower().rstrip("=")


def _color_for(deadline: DeadlineLike, today: date) -> str | None:
    if deadline.due is None:
        return None
    days_left = deadline.days_remaining(today)
    if days_left is not None and days_left <= DUE_SOON_DAYS:
        return COLOR_ID_RED
    return None


def sync_deadlines(
    case_id: str,
    patient_label: str,
    deadlines: list[DeadlineLike],
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
        if d.due is None:
            continue
        event_id = _stable_event_id(case_id, d)
        body = {
            "id": event_id,
            "summary": f"[{d.front.upper()}] {d.name} -- {patient_label}",
            "description": f"{d.explain(today)}\n\nCitation: {d.citation}",
            "start": {"date": d.due.isoformat()},
            "end": {"date": d.due.isoformat()},
        }
        color = _color_for(d, today)
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
        out.append(
            {"front": d.front, "name": d.name, "event_id": event["id"], "due": d.due.isoformat()}
        )
    return out
