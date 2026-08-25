"""Bill-date parsing + Deadline serialization glue (agent_core/casedata.py)."""

from __future__ import annotations

from datetime import date

from agent_core.casedata import parse_bill_dates, serialize_deadline
from rules.deadlines import compute_deadlines


def test_parse_bill_dates_converts_iso_strings():
    bill = {"first_statement_date": "2026-03-01", "amount_cents": 500}
    parsed = parse_bill_dates(bill)
    assert parsed["first_statement_date"] == date(2026, 3, 1)
    assert parsed["amount_cents"] == 500  # untouched


def test_parse_bill_dates_leaves_missing_alone():
    parsed = parse_bill_dates({})
    assert parsed.get("first_statement_date") is None


def test_parse_bill_dates_bad_string_becomes_none_not_crash():
    parsed = parse_bill_dates({"service_date": "not-a-date"})
    assert parsed["service_date"] is None


def test_serialize_deadline_matches_shape():
    bill = parse_bill_dates({"first_statement_date": "2026-01-01"})
    deadlines = compute_deadlines(bill, "TX")  # unlisted state -> federal floor
    charity = next(d for d in deadlines if d.front == "charity_care" and d.due is not None)
    payload = serialize_deadline(charity)
    assert payload["due"] == "2026-08-29"  # +240 days
    assert payload["basis_date"] == "2026-01-01"
    assert "26 CFR 1.501(r)-4" in payload["citation"]
    assert "explain" in payload and isinstance(payload["explain"], str)


def test_serialize_deadline_none_due():
    bill = parse_bill_dates({"first_statement_date": "2026-01-01"})
    deadlines = compute_deadlines(bill, "CA")  # no deadline
    charity = next(
        d for d in deadlines if d.front == "charity_care" and d.name == "Charity care application"
    )
    payload = serialize_deadline(charity)
    assert payload["due"] is None
