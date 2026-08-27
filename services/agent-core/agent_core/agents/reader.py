"""Reader: Gemma first-pass classification + Gemini structured extraction.

Playbook §4 persona 5, WO1: "on `case.document.added` -- Gemma first-pass
classification (bill/denial/collection/GFE/income-proof -- this is the
bonus-point model, make it real and log its output), then Gemini 3.7 Flash
structured extraction into the `bill`/`documents.extracted` schema."

Unlike Clock/Auditor, Reader's "fact" genuinely comes from two LLM calls --
there is no pure-Python bill-field extractor, extraction IS the job. What
still holds is §2.1's spirit: the extraction happens in `genai_client`
*before* the ADK agent turn, over deterministic temperature=0 JSON-schema
calls with retry-on-invalid: the ADK agent's only role is to call a tool
that returns that already-computed result and narrate it for the audit log.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from .. import config, genai_client
from ..factmerge import PATIENT_STATEMENT_TYPE as STATEMENT_TYPE
from . import common

NAME = "reader"

INSTRUCTION = (
    "You are Reader, a document-intake agent for a medical-bill advocacy system. "
    "Call get_reader_result exactly once, then in 1-2 sentences state the document "
    "type Gemma assigned and the most important fields Gemini extracted. Never "
    "invent a field value that is not in the tool result."
)

# Contract §3.1 documents.extracted -- deliberately generic across doc types
# (bill / itemized_bill / denial_letter / collection_notice / gfe /
# income_proof) since one hackathon-scope schema covering all of them is
# simpler than six narrow ones and every field is optional.
EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # `nullable: True` on every scalar field below (added post-live-defect,
        # see this module's docstring extension and _scrub_ungrounded): the
        # original schema gave the model no formally valid way to say "this
        # concept exists but I could not read it" other than the prose
        # instruction "use null/omit fields you cannot find" -- for a
        # `type: string`/`type: integer` field with no null alternative,
        # structured-output decoding can only emit a value of that type, so a
        # model that "cannot find" an EIN or a date sometimes filled the slot
        # with a plausible-looking placeholder instead of leaving it out.
        # `nullable` gives it a real JSON `null` to emit instead.
        "provider_name": {"type": "string", "nullable": True},
        "hospital_ein": {"type": "string", "nullable": True},
        "hospital_ccn": {"type": "string", "nullable": True},
        "patient_name": {
            "type": "string",
            "nullable": True,
            "description": "The PATIENT's name exactly as printed, e.g. a line reading "
            "'Patient: Jordan Alvarez' -> 'Jordan Alvarez'. Not the provider, not the "
            "collector, and not an employee named on a pay stub. Null if the document does "
            "not name a patient.",
        },
        # Added after ef-2026-0001's twin arrived by email with no state at
        # all: `compute_deadlines(bill, state)` (§3.5) switches the ENTIRE
        # charity-care regime on this -- California has no application
        # deadline (Cal. Health & Safety Code §127405(e)(3)), Illinois has a
        # 90-day one -- and with the field absent a case silently gets the
        # federal floors only. The state is printed on the facility
        # letterhead of every bill and GFE this system has ever seen
        # ("Sutter Bay Hospitals / CA -- EIN 94-0562680").
        "state": {
            "type": "string",
            "nullable": True,
            "description": "The two-letter USPS abbreviation of the US state in the "
            "provider/facility address printed on this document's letterhead, e.g. a "
            "letterhead reading 'CA - EIN 94-0562680' -> 'CA'. Two letters only, never the "
            "full state name. Null if no state appears on the document -- never guess one "
            "from the hospital's name or from an area code.",
        },
        # Deliberately phrased as what the DOCUMENT SAYS, not as `insured`:
        # asking a model whether a patient is insured invites it to infer from
        # the absence of a payer line, which is not a fact. 45 CFR 149.610(a)
        # is why a Good Faith Estimate is issued at all, and this repo's
        # fixtures print that citation verbatim -- so on a GFE this is a
        # quotation, not a judgement. `rules.fronts._select_ppdr` refuses on
        # "coverage status unknown", which is the correct outcome when the
        # document is silent.
        "uninsured_self_pay": {
            "type": "boolean",
            "nullable": True,
            "description": "True ONLY if this document EXPLICITLY states the patient is "
            "uninsured or self-pay (e.g. 'Per 45 CFR 149.610 -- provided to an uninsured / "
            "self-pay patient'). False ONLY if it explicitly states the patient HAS coverage "
            "(names an insurer or plan, or shows an insurance payment/adjustment line). Null "
            "if the document does not say either way -- do NOT infer a coverage status from "
            "the absence of any mention of insurance.",
        },
        "amount_cents": {
            "type": "integer",
            "nullable": True,
            "description": "The single TOTAL amount due, e.g. a line reading 'TOTAL: $2,625.00' "
            "-> 262500. This is separate from, and in addition to, every individual line_items "
            "row -- always extract both when the document has a line-item table.",
        },
        "service_date": {"type": "string", "nullable": True, "description": "ISO-8601 date"},
        "first_statement_date": {
            "type": "string",
            "nullable": True,
            "description": "ISO-8601 date",
        },
        "gfe_amount_cents": {"type": "integer", "nullable": True},
        "in_collections": {"type": "boolean", "nullable": True},
        "collector_name": {"type": "string", "nullable": True},
        "validation_notice_date": {
            "type": "string",
            "nullable": True,
            "description": "ISO-8601 date",
        },
        "line_items": {
            "type": "array",
            "description": "Every row of an itemized statement's billing table -- one entry per "
            "CODE/DESCRIPTION/UNITS/CHARGE row, IN THE SAME ORDER they appear, including exact "
            "duplicate rows (a code that is billed twice must appear as two separate entries "
            "here, not merged into one with units=2 -- duplicate-line detection depends on "
            "seeing both original rows). Never skip rows and never summarize; if the document "
            "has no such table, omit this field entirely.",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The billing code, e.g. '99284'."},
                    "description": {"type": "string"},
                    "units": {"type": "integer"},
                    "charge_cents": {
                        "type": "integer",
                        "description": "This row's own charge, e.g. '$1,850.00' -> 185000.",
                    },
                },
            },
        },
        "demanded_documents": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Documents a denial letter says are missing/required.",
        },
        "household_size": {"type": "integer", "nullable": True},
        "annual_income_cents": {"type": "integer", "nullable": True},
        "is_income_proof": {
            "type": "boolean",
            "nullable": True,
            "description": "False for the cat-photo case: an upload that is not "
            "actually an income document at all.",
        },
    },
}

EXTRACTION_INSTRUCTION = (
    "Extract EVERY field you can find from this medical-billing document into the JSON schema -- "
    "do not stop after the first field or two that identify the document; a partial extraction "
    "that only names the hospital is as unhelpful to the patient as no extraction at all. If the "
    "document contains an itemized billing table (columns like CODE, DESCRIPTION, UNITS, CHARGE), "
    "you MUST populate line_items with one entry per row, in order, including exact duplicate "
    "rows verbatim -- AND separately extract the aggregate fields (amount_cents, service_date, "
    "first_statement_date, provider_name, hospital_ein) that appear elsewhere on the same "
    "document; extracting the table does not excuse skipping those. Read the LETTERHEAD too: the "
    "facility's two-letter state goes in `state`, and the line naming the patient goes in "
    "`patient_name`. Set `uninsured_self_pay` only from an explicit statement about coverage on "
    "the document itself (a Good Faith Estimate issued under 45 CFR 149.610 is such a statement); "
    "leave it null when the document is silent rather than inferring coverage. Use JSON null for "
    "any field "
    "you genuinely cannot find -- every field in this schema accepts null. NEVER invent a "
    "plausible-looking placeholder instead: not '00-0000000' or any other made-up EIN/CCN, not "
    "'Unknown'/'N/A' as a name, not 1970-01-01 (Unix epoch) or any other guessed date. If a "
    "document is corrupted, unreadable, or truncated, it is far better to return null for every "
    "field than to fabricate a plausible-sounding one. Dates must be ISO-8601 (YYYY-MM-DD). Money "
    "fields are integer cents. If this document is clearly not a real income document (e.g. a "
    "photo of a pet, a screenshot unrelated to income), set is_income_proof to false."
)

# Belt-and-suspenders for the instruction above: even a well-instructed,
# schema-nullable model can still emit a plausible-looking sentinel instead of
# null (this is exactly what happened live on ef-2026-0006, PROOF's
# deliberately-unparseable-bill fixture -- see fixtures/generated/cases/
# case_06_unparseable_bill/case.json's own docstring-equivalent `notes` field).
# `_scrub_ungrounded` is the actual guarantee: it runs on every extraction,
# regardless of model behavior, and removes values that match known
# fabrication patterns rather than trusting the prompt to have worked.
_MIN_PLAUSIBLE_DATE = date(2000, 1, 1)  # no bill in this system predates this era
_PLACEHOLDER_NAME_STRINGS = {
    "unknown",
    "n/a",
    "na",
    "none",
    "not available",
    "not applicable",
    "unavailable",
    "not provided",
    "tbd",
    "redacted",
}
_DIGITS_RE = re.compile(r"\d")


def _all_same_digit(value: str) -> bool:
    """True for '00-0000000', '000000', '111111', etc -- a real IRS EIN or CMS
    CCN is never every digit identical; a model reaching for *a* plausible ID
    when it has none tends to reach for exactly this shape.
    """
    digits = _DIGITS_RE.findall(value)
    return len(digits) >= 2 and len(set(digits)) == 1


def _is_epoch_or_implausible_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return False
    return parsed < _MIN_PLAUSIBLE_DATE


# (schema field -> implausibility check) for every scalar field a fabricated
# placeholder has actually been observed in, live, on ef-2026-0006.
_STRING_SCRUB_RULES: dict[str, Callable[[str], bool]] = {
    "hospital_ein": _all_same_digit,
    "hospital_ccn": _all_same_digit,
    "provider_name": lambda v: v.strip().lower() in _PLACEHOLDER_NAME_STRINGS,
    # Same rule for the patient: an "Unknown"/"N/A" patient name is a
    # fabricated placeholder, and unlike a provider name it would be printed
    # on a filed FAP application under a real person's claim.
    "patient_name": lambda v: v.strip().lower() in _PLACEHOLDER_NAME_STRINGS,
    "service_date": _is_epoch_or_implausible_date,
    "first_statement_date": _is_epoch_or_implausible_date,
    "validation_notice_date": _is_epoch_or_implausible_date,
}


def _scrub_ungrounded(extraction: dict) -> tuple[dict, list[str]]:
    """Strip fields whose value matches a known fabrication pattern rather
    than trusting the model to have honored the "use null" instruction.

    Returns (cleaned_extraction, names_of_scrubbed_fields). A scrubbed field
    is DROPPED from the dict entirely (equivalent to the model never having
    reported it) -- downstream code (`agent_core.factmerge`,
    `casedata.parse_bill_dates`) already treats an absent field as "unknown",
    never as zero/epoch/empty-string, so dropping is the correct degrade.
    """
    if not isinstance(extraction, dict) or "_extraction_error" in extraction:
        return extraction, []

    cleaned = dict(extraction)
    scrubbed: list[str] = []
    for field, is_implausible in _STRING_SCRUB_RULES.items():
        value = cleaned.get(field)
        if isinstance(value, str) and value.strip() and is_implausible(value):
            del cleaned[field]
            scrubbed.append(field)
    return cleaned, scrubbed


# --- the patient's own words (§3.1 `patient_statement`) --------------------

STATEMENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "household_size": {
            "type": "integer",
            "nullable": True,
            "description": "The number of people in the patient's household, ONLY if the "
            "message states it as a present fact about themselves (e.g. 'Household of "
            "three' -> 3, 'there are 4 of us' -> 4). Null for anything hypothetical, "
            "conditional, future or past ('my son MIGHT move back in', 'if my mother comes "
            "to live with us', 'we USED to be five'), and null if you are counting people "
            "mentioned in the message yourself rather than reading a number the patient "
            "stated. Never infer a household from who is mentioned.",
        },
        "household_size_quote": {
            "type": "string",
            "nullable": True,
            "description": "The words that state the household size, copied from the "
            "message CHARACTER FOR CHARACTER -- not paraphrased, not re-punctuated, not "
            "re-cased. Null whenever household_size is null.",
        },
        "annual_income_cents": {
            "type": "integer",
            "nullable": True,
            "description": "The patient's CURRENT annual household income in integer cents "
            "('about $32,000 a year' -> 3200000), only if the message states it. Null for a "
            "past income ('I used to make $80,000 before I got sick'), an hourly or monthly "
            "figure you would have to multiply out yourself, an amount that is plainly the "
            "BILL rather than an income, or an expectation about the future.",
        },
        "annual_income_quote": {
            "type": "string",
            "nullable": True,
            "description": "The words stating the income, copied character for character. "
            "Null whenever annual_income_cents is null.",
        },
        "uninsured_self_pay": {
            "type": "boolean",
            "nullable": True,
            "description": "True ONLY if the message explicitly says the patient has no "
            "insurance or is paying out of pocket ('I'm uninsured and paying out of "
            "pocket'). False ONLY if it explicitly says they HAVE coverage (names a plan or "
            "insurer). Null if the message does not say either way, if the coverage is "
            "described as lapsed/pending/uncertain, or if you would have to infer it.",
        },
        "coverage_quote": {
            "type": "string",
            "nullable": True,
            "description": "The words stating the coverage status, copied character for "
            "character. Null whenever uninsured_self_pay is null.",
        },
    },
}

STATEMENT_INSTRUCTION = (
    "The text below is the body of an email a patient wrote to a medical-bill advocacy "
    "service. It is NOT a document -- it is a person talking, and what they say will be "
    "treated as their own unverified claim, never as a proven fact. Your job is to record "
    "ONLY what they explicitly stated about themselves, and to return null for everything "
    "else. Prefer null over a guess in every ambiguous case: a null costs the patient one "
    "clarifying question, while a wrong number could erase or fail to erase thousands of "
    "dollars of debt on a claim they never made. Do not infer, do not average, do not count "
    "people up yourself, do not convert a maybe into a yes. A sentence about what MIGHT "
    "happen, what USED to be true, or what someone is CONSIDERING states nothing. For every "
    "field you do fill in, you MUST also return the accompanying *_quote field containing "
    "the exact words from the message that state it, copied character for character -- if "
    "you cannot point to the words, you do not have the fact, and both fields must be null. "
    "Extract nothing about the hospital, the bill, the amount owed or the dates; those come "
    "from documents, not from what someone remembers."
)

#: (value field -> quote field) for every fact a statement may carry.
_STATEMENT_QUOTE_FIELDS: dict[str, str] = {
    "household_size": "household_size_quote",
    "annual_income_cents": "annual_income_quote",
    "uninsured_self_pay": "coverage_quote",
}

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_quote_match(text: str) -> str:
    """Fold the differences a faithful quote may still carry: case, and the
    line wrapping a mail client inserts mid-sentence.

    Nothing else is folded. Punctuation and digits are left exactly as they
    are, because a quote that differs from the message in a DIGIT is not a
    quote, and that is the case this check exists to catch.
    """
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _ground_statement(extraction: dict, doc_text: str) -> tuple[dict, list[str]]:
    """Drop every stated fact whose quote does not literally appear in the
    message. Returns `(cleaned, names_of_ungrounded_fields)`.

    THIS IS THE GUARANTEE, not the prompt. `_scrub_ungrounded` above exists
    because a well-instructed model still emitted `00-0000000` for an EIN it
    could not read; the same posture applies with more force here, because
    prose has no fixed shape to sanity-check a number against. Requiring the
    model to point at the words it read a fact from, and then CHECKING that
    those words are in the message, turns "did it make this up?" from a
    judgement into a substring test. A fabricated fact has to come with a
    fabricated quote, and a fabricated quote is not in the text.

    Both the value and its quote are dropped together, so downstream sees a
    field the message never mentioned -- which `agent_core.statedfacts` and
    `rules.fronts` already handle as "not stated", the honest degrade.
    """
    if not isinstance(extraction, dict) or "_extraction_error" in extraction:
        return extraction, []

    cleaned = dict(extraction)
    haystack = _normalize_for_quote_match(doc_text or "")
    ungrounded: list[str] = []
    for value_field, quote_field in _STATEMENT_QUOTE_FIELDS.items():
        if cleaned.get(value_field) is None:
            cleaned.pop(quote_field, None)
            continue
        quote = cleaned.get(quote_field)
        grounded = (
            isinstance(quote, str)
            and quote.strip() != ""
            and _normalize_for_quote_match(quote) in haystack
        )
        if not grounded:
            cleaned[value_field] = None
            cleaned.pop(quote_field, None)
            ungrounded.append(value_field)
    return cleaned, ungrounded


async def _run_statement(case_id: str, doc_id: str, doc_text: str) -> dict:
    """Reader for a `patient_statement`: extraction only, no classification.

    Gemma is deliberately NOT called. Its job (§4 persona 5 WO1) is to sort an
    incoming document into one of the six §3.1 evidence types, and an email
    body is none of them -- asking would spend a model call to obtain a label
    that must then be thrown away, and the answer would be a wrong one that
    some later reader of the audit trail takes seriously. `services/intake`
    knows what this is because it put the bytes there itself; there is no
    classification question to answer.
    """
    raw_extraction = genai_client.gemini_extract_json(
        doc_text, STATEMENT_SCHEMA, STATEMENT_INSTRUCTION
    )
    extraction, ungrounded = _ground_statement(raw_extraction, doc_text)
    return {
        "case_id": case_id,
        "doc_id": doc_id,
        "label": STATEMENT_TYPE,
        "gemma_raw": "",
        "gemma_error": None,
        "gemma_fallback_used": False,
        "extraction": extraction,
        "scrubbed_fields": [],
        # Distinct from `scrubbed_fields` on purpose: that names a value that
        # LOOKED like a fabricated placeholder; this names a value the model
        # could not point to words for. Two different failures deserve two
        # different sentences in the audit trail.
        "ungrounded_fields": ungrounded,
        "citations": [],
    }


async def run(case_id: str, doc_id: str, doc_text: str, doc_type_hint: str | None = None) -> dict:
    """Classify + extract one document. Returns the fact + LLM narration.

    `doc_text` is the best-effort text for this document (OCR/plaintext -- how
    it got there is RELAY's intake pipeline; this agent only ever sees text).

    A `patient_statement` (the body of the email the bill came attached to)
    takes a different route entirely -- see `_run_statement`. It is prose, not
    a document; nothing it says may become a canonical fact
    (`factmerge.PATIENT_STATEMENT_TYPE`), and every value it yields must be
    backed by a verbatim quote from the message itself.
    """
    model = config.GEMINI_MODEL
    if doc_type_hint == STATEMENT_TYPE:
        fact = await _run_statement(case_id, doc_id, doc_text)
        tool = common.make_fact_tool(
            "get_reader_result",
            "Return what the patient stated in their own words, and what could not be "
            "grounded in the message text.",
            fact,
        )
        prompt = (
            f"The patient's own message (document id={doc_id}) was just added. Call "
            "get_reader_result and state what they claimed about themselves, making clear "
            "that these are their own unverified statements rather than facts from a "
            "document."
        )
        turn = await common.run_agent_turn(NAME, model, INSTRUCTION, [tool], prompt)
        return {"fact": fact, **turn}

    classification = genai_client.gemma_classify(doc_text)
    raw_extraction = genai_client.gemini_extract_json(
        doc_text, EXTRACTION_SCHEMA, EXTRACTION_INSTRUCTION
    )
    extraction, scrubbed_fields = _scrub_ungrounded(raw_extraction)
    label = doc_type_hint or classification["label"]

    fact = {
        "case_id": case_id,
        "doc_id": doc_id,
        "label": label,
        "gemma_raw": classification["raw"],
        "gemma_error": classification["error"],
        "gemma_fallback_used": classification.get("fallback_model_used", False),
        "extraction": extraction,
        # Defect fix (persona 5 WO8, "graceful degradation, not fabrication"):
        # names every field this run discarded as an implausible placeholder
        # (see `_scrub_ungrounded`) so the case's own event log -- not just
        # this module's internals -- says plainly why a fact is missing
        # rather than looking like the document simply never mentioned it.
        "scrubbed_fields": scrubbed_fields,
        "citations": [],
    }
    tool = common.make_fact_tool(
        "get_reader_result",
        "Return this document's Gemma classification and Gemini structured extraction.",
        fact,
    )
    # Deliberately does NOT interpolate `case_id` into the prompt (bug found
    # live 2026-08-25, cross-case leakage): PROOF's demo_reset.py reseeds each
    # case through the live pipeline under a throwaway `demo-{fixture}-{uuid}`
    # id and only renames it to the human-plausible `ef-2026-000N` id
    # AFTERWARDS. An agent's freeform narration is copied verbatim into
    # `events/{id}.detail` (store.append_event) and rename_case only rewrites
    # each event's *structured* `case_id` field -- it cannot scrub a raw id an
    # LLM chose to echo back into prose. An audit trail that names the wrong
    # case is worse than no audit trail. `doc_id` is safe: it is never
    # renamed, so nothing in this prompt can name a case that isn't this one.
    prompt = (
        f"A new document (id={doc_id}) was just added. "
        "Call get_reader_result and summarize what was found."
    )
    turn = await common.run_agent_turn(NAME, model, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
