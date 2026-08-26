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

from .. import config, genai_client
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
        "provider_name": {"type": "string"},
        "hospital_ein": {"type": "string"},
        "hospital_ccn": {"type": "string"},
        "amount_cents": {
            "type": "integer",
            "description": "The single TOTAL amount due, e.g. a line reading 'TOTAL: $2,625.00' "
            "-> 262500. This is separate from, and in addition to, every individual line_items "
            "row -- always extract both when the document has a line-item table.",
        },
        "service_date": {"type": "string", "description": "ISO-8601 date"},
        "first_statement_date": {"type": "string", "description": "ISO-8601 date"},
        "gfe_amount_cents": {"type": "integer"},
        "in_collections": {"type": "boolean"},
        "collector_name": {"type": "string"},
        "validation_notice_date": {"type": "string", "description": "ISO-8601 date"},
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
        "household_size": {"type": "integer"},
        "annual_income_cents": {"type": "integer"},
        "is_income_proof": {
            "type": "boolean",
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
    "document; extracting the table does not excuse skipping those. Use null/omit fields you "
    "genuinely cannot find. Dates must be ISO-8601 (YYYY-MM-DD). Money fields are integer cents. "
    "If this document is clearly not a real income document (e.g. a photo of a pet, a screenshot "
    "unrelated to income), set is_income_proof to false."
)


async def run(case_id: str, doc_id: str, doc_text: str, doc_type_hint: str | None = None) -> dict:
    """Classify + extract one document. Returns the fact + LLM narration.

    `doc_text` is the best-effort text for this document (OCR/plaintext -- how
    it got there is RELAY's intake pipeline; this agent only ever sees text).
    """
    model = config.GEMINI_MODEL
    classification = genai_client.gemma_classify(doc_text)
    extraction = genai_client.gemini_extract_json(
        doc_text, EXTRACTION_SCHEMA, EXTRACTION_INSTRUCTION
    )
    label = doc_type_hint or classification["label"]

    fact = {
        "case_id": case_id,
        "doc_id": doc_id,
        "label": label,
        "gemma_raw": classification["raw"],
        "gemma_error": classification["error"],
        "gemma_fallback_used": classification.get("fallback_model_used", False),
        "extraction": extraction,
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
