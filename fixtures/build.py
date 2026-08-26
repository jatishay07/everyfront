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

AMENDED 2026-08-26 (PROOF, WO7 live-verification pass): the paragraph above
was accurate against the Auditor as it stood before LEDGER's wo6 PR
(`56d913c`, "wire NCCI + reliable cash-price findings into the billing
audit") -- which had already merged to `main` by the time this module's own
"no ptp_lookup/mue_lookup at all, ever" claim was written, but the live
Cloud Run deployment that claim was checked against had not yet been
redeployed past the PREVIOUS commit (confirmed directly: the deployed
revision's source bundle was byte-identical to `f35251d`, three commits
behind). The claim was true of what was LIVE, not of what was actually
MERGED. `services/agent-core/agent_core/agents/auditor.py` today builds
`ptp_lookup`/`mue_lookup` from `agent_core.ncci_cache` (LEDGER's bundled
`packages/datapipes/datapipes/data/ncci.sqlite` snapshot -- no network, so
this offline corpus build can reproduce it exactly) and a `cash_price_lookup`
that PREFERS `hospital["cash_prices"]`, a value LEDGER's seed pipeline writes
into Firestore once per hospital, offline, straight from the same MRF (only
falling back to a live-bounded fetch for codes that pre-cache doesn't cover).
So this module now passes REAL `ptp_lookup`/`mue_lookup` (bundled NCCI,
`_ncci_lookups()` below) and a REAL `cash_price_lookup` per hospital
(`_CASH_PRICES_BY_EIN` below -- a small, dated, provenance-cited snapshot of
the two real hospitals' live-seeded `cash_prices`, captured 2026-08-26 via
`GET /hospitals/{ein}` against the deployed project; Sutter Bay and Prairie
Crossing carry no MRF cash-price data, matching their `Hospital.verification_note`
in cases_data.py). `audit_findings_cents_total` now uses
`rules.audit.total_savings_cents` (the per-line-max dedup rule), not a naive
sum, matching the live Auditor exactly -- see that function's own docstring
for why a naive sum would over-claim once a line can be named by more than
one theory (duplicate AND cash-price-delta, e.g. case_07's repeated 80053
line).
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "packages" / "rules"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "datapipes"))

from rules.audit import PTPEdit, audit_line_items, total_savings_cents  # noqa: E402
from rules.deadlines import compute_deadlines  # noqa: E402
from rules.denial import check_denial_lawfulness  # noqa: E402
from rules.eligibility import screen_eligibility  # noqa: E402
from rules.fronts import select_fronts  # noqa: E402

from fixtures.cases_data import WATERMARK, CaseFixture, Hospital  # noqa: E402

TODAY = date(2026, 8, 25)  # CLAUDE.md currentDate -- see docstring in cases_data.py

# Real, live-seeded cash prices for the two hospitals in this corpus LEDGER's
# MRF pipeline actually resolved a price for (see this module's 2026-08-26
# docstring amendment) -- captured via `GET /hospitals/{ein}` against the
# deployed project on 2026-08-26. Sutter Bay (94-0562680) and Prairie
# Crossing (00-0000001, synthetic) both carry `cash_prices: None` live --
# matching cases_data.py's own `Hospital.verification_note` for each -- so
# they are honestly absent here too, not backfilled with a guess.
_CASH_PRICES_BY_EIN: dict[str, dict[str, int]] = {
    "36-2169147": {  # Advocate Christ Medical Center
        "80053": 10750,
        "71046": 16000,
        "80048": 6250,
        "96365": 39000,
        "36415": 2250,
        "86787": 7000,
        "99285": 169000,
    },
    "94-6174066": {  # Stanford Health Care
        "73610": 73280,
        "29405": 106520,
        "80048": 41400,
        "99283": 199520,
    },
}


def _cash_price_lookup_for(hospital_ein: str | None):
    prices = _CASH_PRICES_BY_EIN.get(hospital_ein or "")
    if not prices:
        return None
    return prices.get


def _ncci_lookups():
    """Real `(ptp_lookup, mue_lookup)` from LEDGER's bundled NCCI snapshot --
    no network, same table `agent_core.ncci_cache` opens live. Degrades to
    `(None, None)` if `packages/datapipes` or its bundled sqlite file isn't
    available in this environment, matching the "never fabricate a finding"
    contract every lookup in `rules.audit` already honors."""
    try:
        from datapipes.ncci import load_default
    except ImportError:
        return None, None
    try:
        table = load_default()
    except Exception:  # noqa: BLE001 -- offline corpus build must not crash
        return None, None

    def ptp_lookup(code_a: str, code_b: str):
        result = table.lookup(code_a, code_b)
        if not result.matched:
            return None
        return PTPEdit(
            column1_code=result.column1,
            column2_code=result.column2,
            modifier_allowed=bool(result.allowed_with_modifier),
        )

    def mue_lookup(code: str):
        result = table.mue(code)
        return result.mue_value if result is not None else None

    return ptp_lookup, mue_lookup


_PTP_LOOKUP, _MUE_LOOKUP = _ncci_lookups()

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
    # Real ptp_lookup/mue_lookup (bundled NCCI, offline) + a real, dated
    # cash_price_lookup snapshot -- see this module's 2026-08-26 docstring
    # amendment for why these are no longer hardcoded to None.
    findings = audit_line_items(
        bill["line_items"],
        ptp_lookup=_PTP_LOOKUP,
        mue_lookup=_MUE_LOOKUP,
        cash_price_lookup=_cash_price_lookup_for(case.bill.get("hospital_ein")),
    )
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
            "audit_findings_cents_total": total_savings_cents(findings),
            "denial_check_reference_model": (asdict(denial_check) if denial_check else None),
        },
    }
