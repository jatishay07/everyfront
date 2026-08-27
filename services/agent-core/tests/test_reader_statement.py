"""Reader over the patient's own prose -- the extraction path where
fabrication risk actually lives.

This project's worst defect (HANDOFF #5) was a model filling an unreadable
bill's fields with plausible-looking values. A PDF at least has a shape to
sanity-check against; free text does not, so the guarantee here cannot be the
prompt. It is `_ground_statement`: every value must arrive with the words it
was read from, and those words must literally appear in the message. A
fabricated fact needs a fabricated quote, and a fabricated quote is not in
the text.

The AMBIGUOUS CASES this suite pins are the ones a careless extractor gets
wrong: a hypothetical, a past tense, a message that says nothing at all, a
quote that has drifted from the source, and a quote whose digits do not match
the value it claims to support.
"""

from __future__ import annotations

import asyncio

import pytest
from agent_core import genai_client
from agent_core.agents import reader

BODY = (
    "Hi -- attached is the bill from my ER visit and the estimate they gave me "
    "beforehand, plus my last pay stub.\n\n"
    "I'm uninsured and paying out of pocket. Household of three, I make about "
    "$32,000 a year.\n\nThank you for any help.\n"
)


def _ground(extraction: dict, body: str = BODY):
    return reader._ground_statement(extraction, body)


# --------------------------------------------------------------------------
# The grounding check
# --------------------------------------------------------------------------
def test_a_quoted_fact_survives():
    cleaned, ungrounded = _ground(
        {
            "household_size": 3,
            "household_size_quote": "Household of three",
            "annual_income_cents": 3_200_000,
            "annual_income_quote": "I make about $32,000 a year",
            "uninsured_self_pay": True,
            "coverage_quote": "I'm uninsured and paying out of pocket",
        }
    )
    assert ungrounded == []
    assert cleaned["household_size"] == 3
    assert cleaned["annual_income_cents"] == 3_200_000
    assert cleaned["uninsured_self_pay"] is True


def test_a_fact_with_no_quote_at_all_is_dropped():
    """The model answered the question but could not point at the words. On
    this exact message that is the difference between reading "Household of
    three" and inferring a household from the fact that the patient said "we"
    once -- and the second is a number nobody stated."""
    cleaned, ungrounded = _ground({"household_size": 4, "household_size_quote": None})
    assert cleaned["household_size"] is None
    assert ungrounded == ["household_size"]


def test_a_fact_whose_quote_is_not_in_the_message_is_dropped():
    """THE FABRICATION CASE. The quote is fluent, plausible, and nowhere in
    what the patient wrote."""
    cleaned, ungrounded = _ground(
        {
            "household_size": 5,
            "household_size_quote": "there are five people living in my home",
        }
    )
    assert cleaned["household_size"] is None
    assert ungrounded == ["household_size"]


def test_a_quote_that_has_been_paraphrased_is_dropped():
    """ "Household of 3" is not what the message says -- it says "Household of
    three". A model that re-renders a quote has stopped quoting, and the
    difference between a transcription and a rendering is exactly where a
    digit can change unnoticed."""
    cleaned, ungrounded = _ground({"household_size": 3, "household_size_quote": "Household of 3"})
    assert cleaned["household_size"] is None
    assert ungrounded == ["household_size"]


def test_line_wrapping_and_case_do_not_break_a_real_quote():
    """The one thing that IS folded: a mail client's hard wrap mid-sentence,
    and capitalisation. Nothing else -- punctuation and digits are compared
    exactly, because a quote that differs in a digit is the case this check
    exists to catch."""
    body = "I'm uninsured and paying out\nof pocket. household of three."
    cleaned, ungrounded = _ground(
        {"household_size": 3, "household_size_quote": "Household of Three"}, body
    )
    assert ungrounded == []
    assert cleaned["household_size"] == 3


def test_an_empty_message_yields_nothing_and_flags_everything_claimed():
    cleaned, ungrounded = _ground(
        {"household_size": 3, "household_size_quote": "Household of three"}, ""
    )
    assert cleaned["household_size"] is None
    assert ungrounded == ["household_size"]


def test_a_null_fact_drops_its_stray_quote_too():
    """A quote with no value behind it is noise on the document record, and
    worse, it looks like a fact somebody dropped."""
    cleaned, ungrounded = _ground(
        {"household_size": None, "household_size_quote": "Household of three"}
    )
    assert "household_size_quote" not in cleaned
    assert ungrounded == []


def test_one_ungrounded_fact_does_not_take_down_the_grounded_ones():
    cleaned, ungrounded = _ground(
        {
            "household_size": 3,
            "household_size_quote": "Household of three",
            "annual_income_cents": 8_000_000,
            "annual_income_quote": "I used to make $80,000 before I got sick",
        }
    )
    assert cleaned["household_size"] == 3
    assert cleaned["annual_income_cents"] is None
    assert ungrounded == ["annual_income_cents"]


def test_an_extraction_error_passes_through_untouched():
    payload = {"_extraction_error": "invalid JSON after retry"}
    cleaned, ungrounded = _ground(payload)
    assert cleaned == payload
    assert ungrounded == []


# --------------------------------------------------------------------------
# The schema and instruction that carry the anti-over-reading rules
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value_field,quote_field",
    [
        ("household_size", "household_size_quote"),
        ("annual_income_cents", "annual_income_quote"),
        ("uninsured_self_pay", "coverage_quote"),
    ],
)
def test_every_stated_value_has_a_quote_field_beside_it(value_field, quote_field):
    """Structural: the grounding check can only work if the schema asks for a
    quote per value. A value field added without one would be ungrounded and
    would sail straight through `_ground_statement`."""
    props = reader.STATEMENT_SCHEMA["properties"]
    assert value_field in props and quote_field in props
    assert props[value_field]["nullable"] is True
    assert props[quote_field]["nullable"] is True
    assert value_field in reader._STATEMENT_QUOTE_FIELDS
    assert reader._STATEMENT_QUOTE_FIELDS[value_field] == quote_field


def test_the_statement_schema_asks_for_nothing_about_the_bill():
    """A patient's recollection of a bill amount or a service date is a worse
    copy of a document that is already on the case. Admitting those fields
    would turn this from "capture what no document can carry" into "let prose
    rewrite the bill"."""
    forbidden = {
        "amount_cents",
        "gfe_amount_cents",
        "service_date",
        "first_statement_date",
        "provider_name",
        "hospital_ein",
        "line_items",
    }
    assert forbidden.isdisjoint(reader.STATEMENT_SCHEMA["properties"])


def test_the_instruction_tells_the_model_to_prefer_null_and_to_quote():
    instruction = reader.STATEMENT_INSTRUCTION.lower()
    assert "prefer null over a guess" in instruction
    assert "do not infer" in instruction
    assert "character for character" in instruction


# --------------------------------------------------------------------------
# run(): a statement never gets classified, and never runs Gemma
# --------------------------------------------------------------------------
def test_a_statement_is_extracted_but_never_classified(monkeypatch):
    """Gemma's six labels are all DOCUMENT types; an email body is none of
    them. Calling it would spend a model call to obtain a label that must then
    be discarded -- and leave a wrong one in the audit trail."""
    classified = []
    monkeypatch.setattr(
        genai_client,
        "gemma_classify",
        lambda text, model=None: classified.append(text) or {"label": "bill", "raw": "bill"},
    )
    monkeypatch.setattr(
        genai_client,
        "gemini_extract_json",
        lambda text, schema, instruction, model=None: {
            "household_size": 3,
            "household_size_quote": "Household of three",
        },
    )
    monkeypatch.setattr(
        reader.common,
        "run_agent_turn",
        lambda *a, **k: _async({"answer": "the patient states a household of three", "trace": []}),
    )

    turn = asyncio.run(reader.run("c1", "body01", BODY, doc_type_hint="patient_statement"))

    assert classified == [], "Gemma was called on prose it has no label for"
    assert turn["fact"]["label"] == "patient_statement"
    assert turn["fact"]["extraction"]["household_size"] == 3
    assert turn["fact"]["ungrounded_fields"] == []


def test_a_statement_run_reports_what_it_could_not_ground(monkeypatch):
    monkeypatch.setattr(genai_client, "gemma_classify", lambda text, model=None: 1 / 0)
    monkeypatch.setattr(
        genai_client,
        "gemini_extract_json",
        lambda text, schema, instruction, model=None: {
            "household_size": 6,
            "household_size_quote": "six of us under one roof",
        },
    )
    monkeypatch.setattr(
        reader.common, "run_agent_turn", lambda *a, **k: _async({"answer": "", "trace": []})
    )

    turn = asyncio.run(reader.run("c1", "body01", BODY, doc_type_hint="patient_statement"))
    assert turn["fact"]["extraction"]["household_size"] is None
    assert turn["fact"]["ungrounded_fields"] == ["household_size"]


def test_a_pdf_still_takes_the_document_path(monkeypatch):
    """The statement branch must not swallow ordinary documents: a bill is
    still classified by Gemma and extracted against the bill schema."""
    seen = {}
    monkeypatch.setattr(
        genai_client,
        "gemma_classify",
        lambda text, model=None: {
            "label": "bill",
            "raw": "bill",
            "error": None,
            "fallback_model_used": False,
        },
    )

    def _extract(text, schema, instruction, model=None):
        seen["schema"] = schema
        return {"amount_cents": 262_500}

    monkeypatch.setattr(genai_client, "gemini_extract_json", _extract)
    monkeypatch.setattr(
        reader.common, "run_agent_turn", lambda *a, **k: _async({"answer": "a bill", "trace": []})
    )

    turn = asyncio.run(reader.run("c1", "doc1", "TOTAL: $2,625.00"))
    assert seen["schema"] is reader.EXTRACTION_SCHEMA
    assert turn["fact"]["label"] == "bill"


async def _async(value):
    return value
