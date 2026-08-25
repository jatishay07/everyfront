"""Small shared conversions between Firestore's JSON-safe case shape and the
Python `date` objects packages/rules expects.

Firestore (and this repo's demo fixtures) store dates as ISO-8601 strings;
`rules.deadlines.compute_deadlines` type-checks with `isinstance(v, date)` and
silently treats a string as "missing" (by design -- see that module's
docstring: an invented deadline is worse than an absent one). Getting this
conversion wrong is exactly the kind of silent-wrong-answer bug docs/SPIKE.md
keeps warning about, so it lives in one place.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime

DATE_FIELDS = (
    "service_date",
    "first_statement_date",
    "validation_notice_date",
    "discharge_date",
    "screening_date",
    "public_program_denial_date",
)


def parse_bill_dates(bill: dict) -> dict:
    """Return a copy of `bill` with every known date field as a `date` object."""
    out = dict(bill)
    for field in DATE_FIELDS:
        v = out.get(field)
        if isinstance(v, str) and v:
            try:
                out[field] = date.fromisoformat(v[:10])
            except ValueError:
                out[field] = None
        elif isinstance(v, datetime):
            out[field] = v.date()
    return out


def serialize_deadline(d) -> dict:
    """`Deadline` dataclass -> JSON-safe dict for Firestore / the REST API."""
    payload = asdict(d) if is_dataclass(d) else dict(d)
    if isinstance(payload.get("due"), date):
        payload["due"] = payload["due"].isoformat()
    if isinstance(payload.get("basis_date"), date):
        payload["basis_date"] = payload["basis_date"].isoformat()
    payload["explain"] = d.explain(today=date.today())
    return payload
