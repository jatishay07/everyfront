"""Fixtures for `POST /demo/inject_bill`.

Two sources, both normalized to the same shape by `load_fixture`:

  1. PROOF's real corpus (persona 7, merged): `fixtures/generated/cases/
     {case_id}/case.json` + `fixtures/generated/hospitals.json` -- the eight
     cases named in FORGE's handoff, `case_01_uninsured_gfe_ca` through
     `case_08_lawful_denial_ca`. Preferred whenever a case_id matches.
  2. `BUILTIN_FIXTURES` below -- three hand-written fixtures kept as a
     fallback (works even if `fixtures/generated/` is absent, e.g. before
     `python -m fixtures.generate` has been run) and because the literal
     playbook acceptance criterion names "maria_uninsured_ca" specifically.

Every consumer gets the same normalized shape:

    {"patient": {...}, "bill": {...},
     "hospital": {ein, ...} | None,          # seed if not already present
     "documents": [{"type": str, "raw_text": str}, ...]}

PROOF's case.json references real, rendered PDF/PNG files (`documents/*.file`)
-- reportlab-drawn bills with real line items, real NCCI-violation-shaped
duplicate lines, real denial-letter demanded-document text, and so on (see
`fixtures/cases_data.py`). `_extract_pdf_text` below reads that real text with
pypdf (the same library RELAY's PDF-fill engine depends on -- see
`packages/delivery/requirements.txt`) FIRST; `_synthesize_raw_text` is now
strictly the fallback for when that fails (a non-PDF file, e.g. the
cat-photo PNG, or PROOF's deliberately-corrupted `case_06_unparseable_bill`
fixture, whose bill.pdf pypdf genuinely cannot parse -- which is exactly the
"degrade gracefully" behavior that fixture exists to exercise) or when a
fixture carries no `file` at all (`BUILTIN_FIXTURES`).

DEFECT FIX (persona 5 WO2, 2026-08-25): `_synthesize_raw_text`'s itemized-bill
reconstruction never included line items at all -- case.json's own `bill`
dict has no `line_items` field (that detail exists only in
`fixtures/cases_data.py`'s `CaseFixture.line_items`, used to RENDER the PDF,
not serialized into case.json). Reader's Gemini extraction schema already
asks for `line_items` (see `services/agent-core/agent_core/agents/reader.py`)
-- it simply had nothing to extract them from, so `audit_line_items()` always
ran against an empty list and `savings_found_cents` was always 0. Reading the
real rendered PDF (which does contain every line item, seeded duplicates
included) fixes this at the actual source instead of teaching the synthesis
path to reinvent a second, parallel copy of PROOF's fixture data.

Every patient here is fictional. Watermarked SYNTHETIC -- DEMO per
BUILD_PLAYBOOK.md rule 0.6: never a real name, SSN, or real patient bill.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pypdf

logger = logging.getLogger("api_core.demo_fixtures")


def _find_fixtures_dir() -> Path:
    """Locate fixtures/ without assuming how deep we are in a directory tree.

    The original `Path(__file__).resolve().parents[3]` counted upward from the
    repo layout -- services/api/api_core/ -> repo root. Cloud Run flattens the
    service to /app, which has only three parents, so the container died on
    IndexError at import time before uvicorn could bind its port. It worked
    perfectly on the dev machine, which is exactly what made it dangerous.

    Search upward for a real fixtures/ directory instead, and let deployment
    override the answer outright. Nothing here depends on tree depth.
    """
    override = os.environ.get("EVERYFRONT_FIXTURES_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        fixtures = candidate / "fixtures"
        if (fixtures / "generated" / "cases").is_dir():
            return fixtures
    # Fall back to a sibling fixtures/ (how deploy.sh stages it into the
    # container) even when the generated cases have not been built yet, so the
    # failure surfaces as an empty fixture list rather than an import crash.
    return Path(__file__).resolve().parents[1] / "fixtures"


_FIXTURES_DIR = _find_fixtures_dir()
_CASES_DIR = _FIXTURES_DIR / "generated" / "cases"
_HOSPITALS_JSON = _FIXTURES_DIR / "generated" / "hospitals.json"

# The two denial-triage cases in PROOF's corpus reference a hospital's
# published FAP document list and a denial letter's demanded-document list.
# Neither is surfaced in the generated case.json (only the pre-computed
# `expected.denial_check_reference_model` outcome is) -- these are transcribed
# directly from fixtures/cases_data.py's `denial_demanded_docs` /
# `denial_fap_published_docs` (the Python source of truth `generate.py`
# renders from) so the denial check runs against PROOF's real intended data,
# not a guess. See that module for case_02 (unlawful) and case_08 (lawful).
_DENIAL_DOC_LISTS: dict[str, dict[str, tuple[str, ...]]] = {
    "case_02_wrongful_denial_il": {
        "demanded": (
            "completed application form",
            "proof of income last 30 days",
            "notarized affidavit of indigency",
            "three years federal tax returns",
        ),
        "fap_published": ("completed application form", "proof of income last 30 days"),
    },
    "case_08_lawful_denial_ca": {
        "demanded": ("completed application form", "proof of income last 30 days"),
        "fap_published": ("completed application form", "proof of income last 30 days"),
    },
}


def _money(cents: int | None) -> str:
    return f"${cents / 100:,.2f}" if isinstance(cents, (int, float)) else "an unstated amount"


def _synthesize_raw_text(case_id: str, doc: dict, case: dict) -> str:
    """Reconstruct plausible document text from case.json's own fields --
    see module docstring for why this exists instead of real PDF/OCR text."""
    patient = case.get("patient") or {}
    bill = case.get("bill") or {}
    doc_type = doc.get("type")
    provider = bill.get("provider_name", "the hospital")
    name = patient.get("name", "the patient")

    if doc_type == "itemized_bill":
        if case_id == "case_06_unparseable_bill":
            # Deliberately not real bill text -- PROOF's own fixture note:
            # "a truncated byte stream inside a plausible-looking header."
            # Reader must fail closed on this, not invent fields.
            return "%PDF-1.4\n[TRUNCATED BYTE STREAM -- FILE CORRUPTED, NOT A VALID PDF]"
        lines = [
            "SYNTHETIC -- DEMO ONLY.",
            f"{provider}, patient statement.",
            f"Patient: {name}. Insured: {patient.get('insured')}.",
            f"Service date: {bill.get('service_date')}. "
            f"First statement date: {bill.get('first_statement_date')}.",
            f"Total amount due: {_money(bill.get('amount_cents'))}.",
        ]
        if bill.get("gfe_amount_cents") is not None:
            lines.append(f"Good faith estimate on file: {_money(bill.get('gfe_amount_cents'))}.")
        if bill.get("in_collections"):
            lines.append(f"Account referred to collections: {bill.get('collector_name')}.")
        lines.append("Financial assistance may be available -- see the hospital's published FAP.")
        return "\n".join(lines)

    if doc_type == "gfe":
        return (
            "SYNTHETIC -- DEMO ONLY. Good Faith Estimate, 45 CFR 149.610.\n"
            f"Patient: {name}. Provider: {provider}.\n"
            f"Estimated total charges: {_money(bill.get('gfe_amount_cents'))}."
        )

    if doc_type == "income_proof":
        if case_id == "case_05_cat_photo_income_proof":
            # PROOF's fixture note: "a synthetic cartoon cat drawing, not a
            # pay stub or tax document" -- Verifier's cat-photo check exists
            # for exactly this. Describe the image; do not invent income data.
            return (
                "[Uploaded image: a synthetic cartoon drawing of a cat. No pay stub, "
                "tax document, or any income or household information is present in "
                "this image.]"
            )
        income = patient.get("annual_income_cents") or patient.get("annual_income")
        return (
            "SYNTHETIC -- DEMO ONLY. Income documentation.\n"
            f"Name: {name}. Household size: {patient.get('household_size')}.\n"
            f"Annual income: {_money(income)}."
        )

    if doc_type == "denial_letter":
        docs = _DENIAL_DOC_LISTS.get(case_id, {}).get(
            "demanded", ("a completed financial-assistance application",)
        )
        return (
            f"SYNTHETIC -- DEMO ONLY. {provider}, financial assistance denial notice.\n"
            f"Patient: {name}.\n"
            "Your application for financial assistance has been DENIED because you did "
            "not submit: " + "; ".join(docs) + ".\n"
            f"Discharge date: {bill.get('service_date')}."
        )

    if doc_type == "collection_notice":
        return (
            f"SYNTHETIC -- DEMO ONLY. {bill.get('collector_name', 'A debt collector')} "
            "collection notice.\n"
            f"This is an attempt to collect a debt of {_money(bill.get('amount_cents'))} "
            f"originally owed to {provider}.\n"
            "You have the right to dispute this debt in writing within 30 days.\n"
            f"Validation notice date: {bill.get('validation_notice_date')}."
        )

    return f"SYNTHETIC -- DEMO ONLY. Document of type {doc_type!r} for {name} at {provider}."


def _extract_pdf_text(path: Path) -> str | None:
    """Real text from a rendered fixture PDF via pypdf. Returns None (never
    raises) on any read/parse failure or empty result -- e.g. a non-PDF file,
    or PROOF's deliberately-corrupted `case_06_unparseable_bill/documents/
    bill.pdf` -- so callers fall back to `_synthesize_raw_text` exactly as
    they did before this existed.
    """
    try:
        reader = pypdf.PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:  # noqa: BLE001 -- any parse failure means "use the synthesis fallback"
        logger.info("could not extract real PDF text from %s; using synthesized text", path)
        return None
    return text or None


def _load_hospitals() -> dict[str, dict]:
    if not _HOSPITALS_JSON.is_file():
        return {}
    return json.loads(_HOSPITALS_JSON.read_text(encoding="utf-8"))


def _load_proof_case(case_id: str) -> dict[str, Any] | None:
    case_json = _CASES_DIR / case_id / "case.json"
    if not case_json.is_file():
        return None
    case = json.loads(case_json.read_text(encoding="utf-8"))

    patient = dict(case.get("patient") or {})
    # PROOF's corpus predates the §3.1 annual_income -> annual_income_cents
    # rename (FORGE amendment, 2026-08-25); the VALUE is already cents (see
    # this module's cross-check against the fixture's own expected.eligibility
    # fpl_pct at review time), only the key is the old name. Carry both so
    # every code path (rules.fronts._income_cents accepts either; this
    # service's own Verifier expects the _cents key) reads the same value.
    if "annual_income" in patient and "annual_income_cents" not in patient:
        patient["annual_income_cents"] = patient["annual_income"]

    bill = dict(case.get("bill") or {})
    hospital = None
    ein = bill.get("hospital_ein")
    if ein:
        hospitals = _load_hospitals()
        record = hospitals.get(ein)
        if record is not None:
            hospital = {"ein": ein, **record}
            fap_docs = _DENIAL_DOC_LISTS.get(case_id, {}).get("fap_published")
            if fap_docs:
                hospital["fap_required_documents"] = list(fap_docs)

    documents = []
    for doc in case.get("documents") or []:
        raw_text = None
        file_rel = doc.get("file")
        if file_rel and str(file_rel).lower().endswith(".pdf"):
            raw_text = _extract_pdf_text(_CASES_DIR / case_id / file_rel)
        if not raw_text:
            raw_text = _synthesize_raw_text(case_id, doc, case)
        documents.append({"type": doc.get("type"), "raw_text": raw_text})

    return {"patient": patient, "bill": bill, "hospital": hospital, "documents": documents}


# Fallback fixtures, used only when fixtures/generated/cases/{name} doesn't
# exist (e.g. before `python -m fixtures.generate` has run) -- and always for
# "maria_uninsured_ca", the literal name BUILD_PLAYBOOK.md's acceptance
# criterion uses. EINs match real Schedule H filings (docs/SPIKE.md gate a).
BUILTIN_FIXTURES: dict[str, dict[str, Any]] = {
    "maria_uninsured_ca": {
        "patient": {
            "name": "SYNTHETIC -- DEMO -- Maria Gonzalez",
            "household_size": 3,
            "annual_income_cents": 24_000_00,
            "insured": False,
            "state": "CA",
        },
        "bill": {
            "hospital_ein": "94-0562680",
            "provider_name": "Sutter Bay Hospitals (SYNTHETIC demo data)",
            "amount_cents": 6_400_00,
            "service_date": "2026-01-10",
            "first_statement_date": "2026-03-01",
            "gfe_amount_cents": 2_000_00,
            "in_collections": False,
        },
        "hospital": {
            "ein": "94-0562680",
            "name": "Sutter Bay Hospitals (SYNTHETIC demo data)",
            "state": "CA",
            "nonprofit": True,
            "free_care_max_fpl_pct": 400,
            "discounted_care_max_fpl_pct": None,
            "source": "SYNTHETIC -- DEMO seed, EIN real per docs/SPIKE.md gate (a)",
            "ccn": "000000",
            "fap_url": None,
            "fap_app_url": None,
            "tax_year": 2024,
            "mrf_url": None,
        },
        "documents": [
            {
                "type": "itemized_bill",
                "raw_text": (
                    "SYNTHETIC -- DEMO ONLY. Sutter Bay Hospitals, patient statement.\n"
                    "Patient: Maria Gonzalez. Uninsured / self-pay.\n"
                    "Service date: 2026-01-10. First statement date: 2026-03-01.\n"
                    "Good faith estimate provided before service: $2,000.00.\n"
                    "Total amount now due: $6,400.00.\n"
                    "Line items:\n"
                    "  99284  Emergency dept visit, level 4   x1   $2,200.00\n"
                    "  71046  Chest X-ray, 2 views             x1   $  350.00\n"
                    "  80053  Comprehensive metabolic panel     x1   $  180.00\n"
                    "  99284  Emergency dept visit, level 4   x1   $2,200.00\n"
                    "Financial assistance is available -- see our published FAP.\n"
                ),
            }
        ],
    },
    "james_il_denial": {
        "patient": {
            "name": "SYNTHETIC -- DEMO -- James Okafor",
            "household_size": 2,
            "annual_income_cents": 30_000_00,
            "insured": True,
            "state": "IL",
        },
        "bill": {
            "hospital_ein": "36-2169147",
            "provider_name": "Advocate Christ Medical Center (SYNTHETIC demo data)",
            "amount_cents": 9_800_00,
            "service_date": "2025-11-02",
            "first_statement_date": "2025-12-01",
            "in_collections": False,
        },
        "hospital": {
            "ein": "36-2169147",
            "name": "Advocate Christ Medical Center (SYNTHETIC demo data)",
            "state": "IL",
            "nonprofit": True,
            "free_care_max_fpl_pct": 250,
            "discounted_care_max_fpl_pct": 600,
            "fap_required_documents": ["Pay stub", "Tax return", "Proof of residency"],
            "source": "SYNTHETIC -- DEMO seed, EIN real per docs/SPIKE.md gate (a)",
            "ccn": "000000",
            "fap_url": None,
            "fap_app_url": None,
            "tax_year": 2024,
            "mrf_url": None,
        },
        "documents": [
            {
                "type": "denial_letter",
                "raw_text": (
                    "SYNTHETIC -- DEMO ONLY. Advocate Christ Medical Center, financial "
                    "assistance denial notice.\n"
                    "Patient: James Okafor.\n"
                    "Your application for financial assistance has been DENIED because "
                    "you did not submit: Pay stub; Notarized affidavit of unemployment; "
                    "Landlord letter confirming rent amount.\n"
                    "Discharge date: 2025-11-02.\n"
                ),
            }
        ],
    },
    "denise_in_collections": {
        "patient": {
            "name": "SYNTHETIC -- DEMO -- Denise Park",
            "household_size": 1,
            "annual_income_cents": 21_000_00,
            "insured": False,
            "state": "CA",
        },
        "bill": {
            "hospital_ein": "94-0562680",
            "provider_name": "Sutter Bay Hospitals (SYNTHETIC demo data)",
            "amount_cents": 3_200_00,
            "service_date": "2025-06-01",
            "first_statement_date": "2025-07-01",
            "in_collections": True,
            "collector_name": "SYNTHETIC -- DEMO Recovery Associates",
            "validation_notice_date": "2026-08-10",
        },
        "hospital": {
            "ein": "94-0562680",
            "name": "Sutter Bay Hospitals (SYNTHETIC demo data)",
            "state": "CA",
            "nonprofit": True,
            "free_care_max_fpl_pct": 400,
            "discounted_care_max_fpl_pct": None,
            "source": "SYNTHETIC -- DEMO seed, EIN real per docs/SPIKE.md gate (a)",
            "ccn": "000000",
            "fap_url": None,
            "fap_app_url": None,
            "tax_year": 2024,
            "mrf_url": None,
        },
        "documents": [
            {
                "type": "collection_notice",
                "raw_text": (
                    "SYNTHETIC -- DEMO ONLY. SYNTHETIC -- DEMO Recovery Associates.\n"
                    "This is an attempt to collect a debt of $3,200.00 originally owed "
                    "to Sutter Bay Hospitals. You have the right to dispute this debt in "
                    "writing within 30 days. Validation notice date: 2026-08-10.\n"
                ),
            }
        ],
    },
    # Defect #2 regression fixture (persona 5 WO2): deliberately carries NO
    # hospital_ein anywhere -- not on the bill, not pre-seeded via `hospital`
    # below (None, so `/demo/inject_bill` does not self-seed one either), and
    # the bill's own text never prints an EIN (realistic: "a bill rarely
    # prints an EIN", per the work order). The ONLY way this case's hospital
    # can resolve is Lookup matching `provider_name` against LEDGER's already-
    # seeded `hospitals/94-0562680` record ("Sutter Bay Hospitals") by name.
    # If that path ever regresses back to EIN-only lookup, this fixture is the
    # one that goes back to "could not be resolved."
    "kim_no_ein_on_bill": {
        "patient": {
            "name": "SYNTHETIC -- DEMO -- Kim Alverson",
            "household_size": 2,
            "annual_income_cents": 20_000_00,
            "insured": False,
            "state": "CA",
        },
        "bill": {
            "provider_name": "Sutter Bay Hospitals",
            "amount_cents": 1_800_00,
            "service_date": "2026-04-02",
            "first_statement_date": "2026-05-01",
            "in_collections": False,
        },
        "hospital": None,
        "documents": [
            {
                "type": "itemized_bill",
                "raw_text": (
                    "SYNTHETIC -- DEMO ONLY. Sutter Bay Hospitals, patient statement.\n"
                    "Patient: Kim Alverson. Uninsured / self-pay.\n"
                    "Service date: 2026-04-02. First statement date: 2026-05-01.\n"
                    "Total amount now due: $1,800.00.\n"
                    "Financial assistance is available -- see our published FAP.\n"
                ),
            }
        ],
    },
    # Defect #4 regression fixture (persona 5 WO2): a denial letter against a
    # hospital record that carries NO `fap_required_documents` -- the real
    # shape of every hospital LEDGER's actual pipeline seeds (contract §3.1's
    # `hospitals/{ein}` has no such field; only two of PROOF's hand-built
    # fixtures inject one). `check_denial_lawfulness` runs but reports
    # `insufficient_data`. Before this fix, `denial_flag` stayed at the
    # store's bare `None` default in exactly this situation -- i.e. for
    # every real (non-fixture) denial case, not just for "no denial letter at
    # all." This fixture proves it now resolves to a definite
    # `{"violated": False, ...}` object instead.
    "morgan_denial_no_fap_list_known": {
        "patient": {
            "name": "SYNTHETIC -- DEMO -- Morgan Reyes",
            "household_size": 2,
            "annual_income_cents": 30_000_00,
            "insured": True,
            "state": "CA",
        },
        "bill": {
            # Stanford, not Advocate: case_02/case_08 inject `fap_required_
            # documents` onto Advocate's Firestore record (36-2169147) the
            # first time either of THOSE fixtures runs, and it persists for
            # every later case against that EIN in the same environment --
            # which would silently make this fixture stop exercising
            # insufficient_data after the first `case_02` demo run. Stanford
            # is never a denial-fixture hospital, so its record never
            # acquires that field.
            "provider_name": "Stanford Health Care",
            "amount_cents": 2_400_00,
            "service_date": "2026-01-05",
            "first_statement_date": "2026-01-20",
            "in_collections": False,
        },
        "hospital": None,  # resolve purely against LEDGER's real seed, no fap_required_documents
        "documents": [
            {
                "type": "denial_letter",
                "raw_text": (
                    "SYNTHETIC -- DEMO ONLY. Stanford Health Care, financial "
                    "assistance denial notice.\n"
                    "Patient: Morgan Reyes.\n"
                    "Your application for financial assistance has been DENIED because "
                    "you did not submit: completed application form; proof of income "
                    "last 30 days.\n"
                    "Discharge date: 2026-01-05.\n"
                ),
            }
        ],
    },
}


def load_fixture(name: str) -> dict[str, Any] | None:
    proof_case = _load_proof_case(name)
    if proof_case is not None:
        return proof_case
    return BUILTIN_FIXTURES.get(name)


def available_fixtures() -> list[str]:
    names = set(BUILTIN_FIXTURES)
    if _CASES_DIR.is_dir():
        names.update(p.name for p in _CASES_DIR.iterdir() if p.is_dir())
    return sorted(names)
