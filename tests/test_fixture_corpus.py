"""Synthetic patient corpus tests -- PROOF (persona 7), work order 1.

Checks the corpus itself: schema shape against contract §3.1, the §2.6 state
fixture rule (CA/IL only), the watermark rule (§0.6 / CLAUDE.md), and that
the committed `fixtures/generated/` bundle has not drifted from
`fixtures/cases_data.py` -- the single source of truth (see
`fixtures/generate.py`'s docstring: nothing under `generated/` is hand-edited).

Where the corpus's `expected` block encodes deadlines and eligibility, this
also cross-checks it against the REAL `packages/rules` functions (they exist
today). Where it encodes fronts/audit findings/denial lawfulness, it checks
against `fixtures/reference_model.py` -- an explicit stand-in for STATUTE's
not-yet-built `select_fronts` / `audit_line_items` / `check_denial_lawfulness`
(see that module's docstring for the HANDOFF).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from rules.deadlines import compute_deadlines
from rules.eligibility import screen_eligibility

from fixtures.build import build_case_json, hospital_to_contract
from fixtures.cases_data import CASES, CASES_BY_ID, HOSPITALS, WATERMARK

# Deliberately NOT importing fixtures/generate.py here: it needs reportlab
# (for the PDF renderers) at module level, while fixtures/build.py -- what
# this file actually exercises -- has no such dependency, so these schema/
# deadline/eligibility/watermark checks run in any environment that already
# runs the rest of the suite. tests/test_bill_pdfs.py is where the
# reportlab/pypdf-dependent checks live, gated by pytest.importorskip.

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "fixtures" / "generated"

EIN_RE = re.compile(r"^\d{2}-\d{7}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")

CONTRACT_PATIENT_KEYS = {"name", "household_size", "annual_income_cents", "insured", "state"}
CONTRACT_BILL_KEYS = {
    "hospital_ein",
    "provider_name",
    "amount_cents",
    "service_date",
    "first_statement_date",
    "gfe_amount_cents",
    "in_collections",
    "collector_name",
    "validation_notice_date",
}
CONTRACT_HOSPITAL_KEYS = {
    "name",
    "ccn",
    "state",
    "fap_url",
    "fap_app_url",
    "free_care_max_fpl_pct",
    "discounted_care_max_fpl_pct",
    "source",
    "tax_year",
    "mrf_url",
}
# `nonprofit` is NOT in the §3.1 hospitals/{ein} shape as written, but
# packages/rules/rules/eligibility.py::screen_eligibility already reads
# `hospital.get("nonprofit", True)` -- and defaulting a for-profit hospital
# to `True` would wrongly grant it a federal 501(r) front. This fixture keeps
# `nonprofit` on every hospital record rather than silently mis-modeling
# case_04's for-profit hospital. HANDOFF -> FORGE: add `nonprofit: bool` to
# the §3.1 contract text; it is already load-bearing in shipped code.
CONTRACT_HOSPITAL_KEYS_WITH_KNOWN_GAP = CONTRACT_HOSPITAL_KEYS | {"nonprofit"}


def _load(case_id: str) -> dict:
    return json.loads((GENERATED / "cases" / case_id / "case.json").read_text())


class TestCorpusShape:
    def test_exactly_eight_cases(self):
        assert len(CASES) == 8

    def test_every_case_id_is_unique_and_snake_case(self):
        ids = [c.case_id for c in CASES]
        assert len(ids) == len(set(ids))
        for cid in ids:
            assert re.match(r"^case_\d\d_[a-z0-9_]+$", cid), cid

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_state_fixture_rule_ca_or_il_only(self, case_id):
        """§2.6: demo cases live in CA (no deadline) or IL (90-day clock) only."""
        assert CASES_BY_ID[case_id].patient["state"] in ("CA", "IL")

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_patient_shape_matches_contract_3_1(self, case_id):
        patient = _load(case_id)["patient"]
        assert set(patient) == CONTRACT_PATIENT_KEYS
        assert isinstance(patient["household_size"], int) and patient["household_size"] >= 1
        assert (
            isinstance(patient["annual_income_cents"], int) and patient["annual_income_cents"] >= 0
        )
        assert isinstance(patient["insured"], bool)
        assert STATE_RE.match(patient["state"])

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_bill_shape_matches_contract_3_1(self, case_id):
        bill = _load(case_id)["bill"]
        # discharge_date is an allowed extra key (IL's "latest of" trigger
        # needs it; deadlines.py reads it directly from the bill dict).
        # line_items is also allowed -- not in §3.1 as written, but needed
        # so a real bill's itemized detail survives into the contract shape
        # at all (see fixtures/build.py's HANDOFF note re: audit_findings_cents
        # always being 0 against the live pipeline without it).
        assert set(bill) >= CONTRACT_BILL_KEYS
        assert set(bill) <= CONTRACT_BILL_KEYS | {"discharge_date", "line_items"}
        if bill["hospital_ein"] is not None:
            assert EIN_RE.match(bill["hospital_ein"]), bill["hospital_ein"]
        assert isinstance(bill["line_items"], list)
        for li in bill["line_items"]:
            assert set(li) == {"code", "description", "units", "charge_cents"}
            assert isinstance(li["units"], int) and li["units"] >= 1
            assert isinstance(li["charge_cents"], int) and li["charge_cents"] >= 0

    def test_every_hospital_matches_contract_3_1(self):
        for ein, h in HOSPITALS.items():
            assert EIN_RE.match(ein)
            record = hospital_to_contract(h)
            assert set(record) == CONTRACT_HOSPITAL_KEYS_WITH_KNOWN_GAP

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_hospital_referenced_by_bill_exists_or_is_honestly_unresolved(self, case_id):
        ein = _load(case_id)["bill"]["hospital_ein"]
        assert ein is None or ein in HOSPITALS

    def test_at_least_one_real_and_one_synthetic_hospital(self):
        sources = {h.source for h in HOSPITALS.values()}
        assert "schedule_h" in sources, "must keep at least one real Schedule-H-verified hospital"
        assert "synthetic_fixture" in sources, "must keep the honest for-profit fixture hospital"

    def test_a_for_profit_hospital_exists_for_the_honest_path(self):
        assert any(not h.nonprofit for h in HOSPITALS.values())


class TestWatermark:
    """Rule 0.6 (BUILD_PLAYBOOK.md §0 / CLAUDE.md): every fixture is fake and
    every case must say so loudly enough that nobody mistakes it for a real
    patient record."""

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_case_json_carries_the_synthetic_notice(self, case_id):
        notice = _load(case_id)["synthetic_data_notice"]
        assert WATERMARK in notice
        assert "fictional" in notice.lower()

    def test_watermark_text_is_the_literal_required_string(self):
        assert WATERMARK == "SYNTHETIC — DEMO"


class TestNoRealPatientData:
    """The corpus must never contain a real SSN. CI's secrets job already
    greps the whole repo for the `NNN-NN-NNNN` pattern; this test double-checks
    scoped to the fixture bundle so a PROOF-only regression fails fast, in
    this package's own suite rather than only in the repo-wide CI job."""

    SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_no_ssn_shaped_string_anywhere_in_the_case(self, case_id):
        blob = json.dumps(_load(case_id))
        assert not self.SSN_RE.search(blob), f"SSN-shaped string found in {case_id}"


class TestNoGeneratedDrift:
    """`fixtures/generated/` must always be exactly what `generate.py` would
    produce from `cases_data.py` right now -- nothing under `generated/` is
    hand-edited (see generate.py's docstring). This is the guard against
    someone patching a committed case.json instead of the source of truth."""

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_case_json_matches_freshly_built_case_json(self, case_id):
        case = CASES_BY_ID[case_id]
        fresh = build_case_json(case, HOSPITALS)
        committed = _load(case_id)
        # Compare through a JSON round-trip so date objects vs. ISO strings
        # don't produce a false mismatch.
        fresh_rt = json.loads(json.dumps(fresh, default=str))
        assert fresh_rt == committed

    def test_hospitals_json_matches_freshly_built(self):
        fresh = {ein: hospital_to_contract(h) for ein, h in HOSPITALS.items()}
        committed = json.loads((GENERATED / "hospitals.json").read_text())
        assert fresh == committed


class TestExpectedDeadlinesMatchTheRealRulesEngine:
    """packages/rules/rules/deadlines.py::compute_deadlines already exists and
    is STATUTE's real, tested code -- so the corpus's `expected.deadlines`
    must match it exactly, not just PROOF's belief about what it should say.
    """

    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_deadlines_recomputed_match_the_fixture(self, case_id):
        case = CASES_BY_ID[case_id]
        bill = dict(case.bill)
        if not (bill.get("service_date") or bill.get("first_statement_date")):
            pytest.skip("no dates to compute a deadline from (case_06)")
        real = compute_deadlines(bill, case.patient["state"], insured=case.patient.get("insured"))
        fixture = _load(case_id)["expected"]["deadlines"]
        assert len(real) == len(fixture)
        for r, f in zip(real, fixture, strict=True):
            assert r.name == f["name"]
            assert (r.due.isoformat() if r.due else None) == f["due"]
            assert r.citation == f["citation"]

    def test_case_02_deadline_is_dramatically_close_as_of_current_date(self):
        """Sanity-check the demo narrative: as of CLAUDE.md currentDate
        2026-08-25, case 2's federal FAP window must be closing soon."""
        d = _load("case_02_wrongful_denial_il")["expected"]["deadlines"][0]
        assert d["name"] == "Charity care application"
        assert 0 <= d["days_remaining_as_of_2026_08_25"] <= 14

    def test_case_07_carries_four_concurrent_deadlines(self):
        """The flagship 'multiple clocks at once' case really has four."""
        ds = _load("case_07_il_concurrent_clocks")["expected"]["deadlines"]
        assert len(ds) == 4
        dues = {d["due"] for d in ds}
        assert len(dues) == 3, "ECA moratorium and PPDR are expected to coincide"


class TestExpectedEligibilityMatchesTheRealRulesEngine:
    @pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
    def test_eligibility_recomputed_matches_the_fixture(self, case_id):
        case = CASES_BY_ID[case_id]
        hospital = HOSPITALS.get(case.bill.get("hospital_ein") or "")
        fixture = _load(case_id)["expected"]["eligibility"]
        if hospital is None:
            assert fixture is None
            return
        real = screen_eligibility(
            case.patient["annual_income_cents"],
            case.patient["household_size"],
            case.patient["state"],
            hospital_to_contract(hospital),
        )
        assert real.determination == fixture["determination"]
        assert real.fpl_pct == fixture["fpl_pct"]

    def test_case_04_forprofit_still_screens_eligible_via_il_state_floor(self):
        """The nuance this case exists to prove: for-profit + IL means the
        FEDERAL front is off, but eligibility math (state floor) still
        clears -- these are two different questions."""
        d = _load("case_04_forprofit_il")
        assert d["expected"]["eligibility"]["determination"] == "discounted"
        fronts = {f["front"]: f["applicable"] for f in d["expected"]["fronts_reference_model"]}
        assert fronts["charity_care"] is False

    def test_case_08_is_ineligible_on_the_merits(self):
        d = _load("case_08_lawful_denial_ca")
        assert d["expected"]["eligibility"]["determination"] == "ineligible"


class TestDenialTriage:
    def test_case_02_is_flagged_unlawful(self):
        check = _load("case_02_wrongful_denial_il")["expected"]["denial_check_reference_model"]
        assert check["unlawful"] is True
        assert check["undisclosed_docs"]

    def test_case_08_is_lawful_no_flag(self):
        check = _load("case_08_lawful_denial_ca")["expected"]["denial_check_reference_model"]
        assert check["unlawful"] is False
        assert check["undisclosed_docs"] == []

    def test_only_cases_with_a_denial_letter_carry_a_denial_check(self):
        with_letter = {"case_02_wrongful_denial_il", "case_08_lawful_denial_ca"}
        for case in CASES:
            has_check = _load(case.case_id)["expected"]["denial_check_reference_model"] is not None
            assert has_check == (case.case_id in with_letter)


class TestFrontOrdering:
    def test_debt_validation_sequences_before_other_fronts(self):
        """§4 persona 3 WO3: in_collections must sequence first."""
        fronts = _load("case_03_in_collections_ca")["expected"]["fronts_reference_model"]
        assert fronts[0]["front"] == "debt_validation"

    def test_audit_always_applies_when_line_items_exist(self):
        for case in CASES:
            if not case.line_items:
                continue
            fronts = _load(case.case_id)["expected"]["fronts_reference_model"]
            audit = next(f for f in fronts if f["front"] == "audit")
            assert audit["applicable"] is True

    def test_ppdr_only_for_uninsured_cases_with_a_qualifying_gfe_delta(self):
        ppdr_cases = {
            c.case_id
            for c in CASES
            if any(
                f["front"] == "ppdr" and f["applicable"]
                for f in _load(c.case_id)["expected"]["fronts_reference_model"]
            )
        }
        assert ppdr_cases == {"case_01_uninsured_gfe_ca", "case_07_il_concurrent_clocks"}
