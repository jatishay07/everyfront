"""Assemble `cases_data.py` into the contract-shaped JSON documents.

Split out of `generate.py` deliberately: this module has NO reportlab/PIL
dependency, only `packages/rules` (which every other test in this repo
already requires). That means `tests/test_fixture_corpus.py` and
`tests/test_stats_consistency.py` -- the schema/watermark/deadline/
eligibility/stats checks -- can run in ANY environment that already runs the
rest of the suite, including today's CI (.github/workflows/ci.yml installs
`ruff pytest pytest-cov` only; see fixtures/requirements.txt's HANDOFF to
FORGE). Only `generate.py`'s actual PDF rendering, and `tests/test_bill_pdfs.py`,
need reportlab/pypdf/Pillow and `pytest.importorskip` themselves out without
those installed.

REWIRED 2026-08-25 (PROOF, WO6): this used to compute `fronts`/
`audit_findings`/`denial_check` via `fixtures/reference_model.py`'s
`select_fronts_reference` / `audit_line_items_reference` /
`check_denial_lawfulness_reference` -- an explicit, admitted stand-in that
module's own docstring says to delete "when STATUTE ships the real
select_fronts / audit_line_items / check_denial_lawfulness." STATUTE shipped
all three (`packages/rules/rules/{fronts,audit,denial}.py`); this module now
imports and calls THOSE directly, so the corpus's `expected` block is checked
against the actual rules engine the live pipeline runs, not a from-scratch
lookalike (`tests/test_fronts_against_fixture_corpus.py` flagged exactly this
gap: "the fixture corpus's committed, generated JSON currently tests a
placeholder, not this package's actual select_fronts"). `fixtures/
reference_model.py` is deleted -- its own docstring said to delete it the day
this switch happened, and nothing outside this comment referenced it as a
Python import (only in docstrings, now updated).

`audit_line_items` accepts optional `ptp_lookup`/`mue_lookup`/
`cash_price_lookup` callables (LEDGER's NCCI/MRF data). This module
deliberately passes NONE of them -- matching exactly what the live pipeline's
Auditor does today (`services/agent-core/agent_core/agents/auditor.py`'s
`_facts()` calls `rules_bridge.audit_line_items(items,
cash_price_lookup=cash_price_lookup)` with no `ptp_lookup`/`mue_lookup` at
all; `cash_price_lookup` itself is None unless a *live* MRF fetch succeeds
against the resolved hospital's real `mrf_url`, which this offline corpus
build can't reproduce deterministically). Concretely this means
`cases_data.py`'s seeded `ptp_unbundling` and `mue_excess` LineItem findings
will NEVER be counted in `audit_findings_cents_total` here, because they are
structurally unreachable in the live system too -- see this PR's HANDOFF to
SWARM/LEDGER. Only `exact_duplicate` findings (detected unconditionally, no
external table needed) count today; a live MRF fetch may ALSO surface a real
`cash_price_delta` finding this static corpus doesn't predict -- that would
be a pleasant surplus, never a promise this corpus makes and fails to keep.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "packages" / "rules"))

from rules.audit import audit_line_items  # noqa: E402
from rules.deadlines import compute_deadlines  # noqa: E402
from rules.denial import check_denial_lawfulness  # noqa: E402
from rules.eligibility import screen_eligibility  # noqa: E402
from rules.fronts import select_fronts  # noqa: E402

from fixtures.cases_data import WATERMARK, CaseFixture, Hospital  # noqa: E402

TODAY = date(2026, 8, 25)  # CLAUDE.md currentDate -- see docstring in cases_data.py

# renderer name -> path relative to a case's directory. The single source of
# truth for where each document ends up; generate.py's RENDERERS re-exports
# these paths alongside the reportlab/PIL function that produces them.
DOCUMENT_PATHS = {
    "bill_pdf": "documents/bill.pdf",
    "corrupted_bill_pdf": "documents/bill.pdf",
    "gfe_pdf": "documents/gfe.pdf",
    "pay_stub_pdf": "documents/income_proof.pdf",
    "denial_letter_pdf": "documents/denial_letter.pdf",
    "collection_notice_pdf": "documents/collection_notice.pdf",
    "cat_photo_png": "documents/income_proof.png",
}


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {o!r}")


def hospital_to_contract(h: Hospital) -> dict:
    """Exactly the §3.1 `hospitals/{ein}` shape (+ `nonprofit`, see
    tests/test_fixture_corpus.py's CONTRACT_HOSPITAL_KEYS_WITH_KNOWN_GAP for
    why that extra key is there deliberately)."""
    return {
        "name": h.name,
        "ccn": h.ccn,
        "state": h.state,
        "nonprofit": h.nonprofit,
        "fap_url": h.fap_url,
        "fap_app_url": h.fap_app_url,
        "free_care_max_fpl_pct": h.free_care_max_fpl_pct,
        "discounted_care_max_fpl_pct": h.discounted_care_max_fpl_pct,
        "source": h.source,
        "tax_year": h.tax_year,
        "mrf_url": h.mrf_url,
    }


def build_case_json(case: CaseFixture, hospitals: dict[str, Hospital]) -> dict:
    hospital = hospitals.get(case.bill.get("hospital_ein") or "")
    amount_cents = sum(li.total_cents for li in case.line_items) or None

    bill = dict(case.bill)
    bill["amount_cents"] = amount_cents
    gfe_specs = [d for d in case.documents if d.render == "gfe_pdf"]
    gfe_delta = gfe_specs[0].kwargs["gfe_delta_cents"] if gfe_specs else None
    bill["gfe_amount_cents"] = amount_cents - gfe_delta if gfe_delta and amount_cents else None
    # HANDOFF -> SWARM: `line_items` is not in §3.1's `bill` shape (like
    # `discharge_date`, it's an allowed extra key -- see
    # tests/test_fixture_corpus.py's CONTRACT_BILL_KEYS). It was entirely
    # absent from the committed case.json until this pass, which is exactly
    # why `/demo/inject_bill` (services/api/api_core/demo_fixtures.py) had
    # nothing to render into the itemized-bill document text, so the Reader
    # never had a code/unit/charge to extract and `audit_line_items` always
    # ran over an empty list -- audit_findings_cents was 0 on every live
    # case verified against this PR (see PR description). Field names match
    # services/agent-core/agent_core/agents/reader.py's EXTRACTION_SCHEMA
    # (`code`, `description`, `units`, `charge_cents`) so a consumer can wire
    # this straight into that shape without a translation layer.
    bill["line_items"] = [
        {
            "code": li.code,
            "description": li.description,
            "units": li.units,
            "charge_cents": li.unit_charge_cents,
        }
        for li in case.line_items
    ]

    deadlines = []
    has_service_date = isinstance(bill.get("service_date"), date)
    has_statement_date = isinstance(bill.get("first_statement_date"), date)
    if has_service_date or has_statement_date:
        deadlines = compute_deadlines(
            bill, case.patient["state"], insured=case.patient.get("insured")
        )

    eligibility = None
    if hospital is not None:
        eligibility = screen_eligibility(
            case.patient["annual_income_cents"],
            case.patient["household_size"],
            case.patient["state"],
            hospital_to_contract(hospital),
        )

    documents = [
        {"doc_id": d.doc_id, "type": d.type, "file": DOCUMENT_PATHS[d.render]}
        for d in case.documents
    ]

    # Real STATUTE code from here down (see this module's 2026-08-25
    # docstring amendment) -- the same `select_fronts`/`audit_line_items`/
    # `check_denial_lawfulness` the live pipeline calls, given the same
    # case-shaped dict the Strategist assembles (patient/bill/hospital/
    # documents).
    fronts_case = {
        "patient": case.patient,
        "bill": bill,
        "hospital": hospital_to_contract(hospital) if hospital is not None else {},
        "documents": [{"type": d.type} for d in case.documents],
    }
    fronts = select_fronts(fronts_case, today=TODAY)
    # No ptp_lookup/mue_lookup/cash_price_lookup -- see docstring: this
    # matches the live Auditor's own call exactly, so a fixture never
    # promises a dollar amount the deployed pipeline can't produce.
    findings = audit_line_items(bill["line_items"])
    denial_check = None
    if case.denial_demanded_docs:
        denial_check = check_denial_lawfulness(
            list(case.denial_demanded_docs), list(case.denial_fap_published_docs)
        )

    return {
        "case_id": case.case_id,
        "title": case.title,
        "proves": case.proves,
        "synthetic_data_notice": (
            f"{WATERMARK}. This patient and every document in this case are fictional. "
            "Rule 0.6 (BUILD_PLAYBOOK.md §0 / CLAUDE.md)."
        ),
        "status": "intake",
        "patient": case.patient,
        "bill": bill,
        "documents": documents,
        "notes": case.notes,
        "expected": {
            "eligibility": (
                {
                    "determination": eligibility.determination,
                    "fpl_pct": eligibility.fpl_pct,
                    "free_threshold_pct": eligibility.free_threshold_pct,
                    "discounted_threshold_pct": eligibility.discounted_threshold_pct,
                    "citations": eligibility.citations,
                    "notes": eligibility.notes,
                }
                if eligibility is not None
                else None
            ),
            "deadlines": [
                {
                    "front": d.front,
                    "name": d.name,
                    "due": d.due,
                    "basis_date": d.basis_date,
                    "basis_field": d.basis_field,
                    "citation": d.citation,
                    "days": d.days,
                    "days_remaining_as_of_2026_08_25": d.days_remaining(TODAY),
                }
                for d in deadlines
            ],
            # Keys keep the `_reference_model` suffix for consumer
            # compatibility (tests/, demo_run.py) even though the VALUES
            # below now come from the real `packages/rules` functions, not a
            # stand-in -- see this module's 2026-08-25 docstring amendment.
            "fronts_reference_model": [asdict(f) for f in fronts],
            "audit_findings_reference_model": [asdict(f) for f in findings],
            "audit_findings_cents_total": sum(f.potential_savings_cents or 0 for f in findings),
            "denial_check_reference_model": (asdict(denial_check) if denial_check else None),
        },
    }
