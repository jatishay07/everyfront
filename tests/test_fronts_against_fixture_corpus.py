"""select_fronts vs. the REAL 8-case corpus -- STATUTE, wo6 citation audit.

Task 4 of the wo6 work order: "The live pipeline now runs [select_fronts]
against PROOF's 8-case corpus. Make sure the ordering rule holds -- debt
validation FIRST when in collections -- and that a front is never marked
applicable without a deadline that can actually be met."

IMPORTANT GAP THIS FILE EXISTS TO CLOSE: `fixtures/build.py` (and therefore
every `fronts_reference_model` entry in `fixtures/generated/cases/*/case.json`
and every assertion in `tests/test_fixture_corpus.py::TestFrontOrdering`) is
still wired to `fixtures/reference_model.py::select_fronts_reference` -- an
explicit, simplified STAND-IN PROOF wrote before STATUTE's real
`rules.fronts.select_fronts` existed. That module's own docstring says the
switch to the real function is supposed to happen automatically once it
ships; it has not been rewired. Concretely, the reference model does not
even implement deadline-expiry gating (no case in the FAP/PPDR/debt-validation
window ever expires in it) or the "sequenced after debt validation" reason
annotation. So the fixture corpus's *committed, generated* JSON currently
tests a placeholder, not this package's actual `select_fronts` -- the exact
kind of "looks checked but isn't" gap agreement §2.2 warns about.

This file is STATUTE's own regression suite (packages/rules is STATUTE's
directory; this repo's existing convention already puts STATUTE's tests for
`packages/rules` under the shared `tests/` -- see test_fronts.py,
test_deadlines.py, etc.) exercising the REAL `select_fronts` directly against
PROOF's real corpus data (`fixtures.cases_data`), independent of the stale
reference model. A HANDOFF to PROOF to rewire `fixtures/build.py` itself
(outside packages/rules/, so not STATUTE's to edit) is in this work order's
PR description.
"""

from __future__ import annotations

from datetime import date

from rules.fronts import FRONT_ORDER, select_fronts

from fixtures.build import hospital_to_contract
from fixtures.cases_data import CASES, HOSPITALS

# CLAUDE.md currentDate, matching fixtures/build.py's TODAY and cases_data.py's
# demo narrative (case_02's ~8-day-remaining federal deadline etc.).
TODAY = date(2026, 8, 25)


def _real_case(case) -> dict:
    """Build a `select_fronts`-shaped case dict straight from the fixture's
    own source of truth (`fixtures.cases_data.CASES`), not from any
    reference-model reshaping."""
    hospital = HOSPITALS.get(case.bill.get("hospital_ein") or "")
    bill = dict(case.bill)
    amount_cents = sum(li.total_cents for li in case.line_items) or None
    bill["amount_cents"] = amount_cents
    gfe_specs = [d for d in case.documents if d.render == "gfe_pdf"]
    gfe_delta = gfe_specs[0].kwargs["gfe_delta_cents"] if gfe_specs else None
    bill["gfe_amount_cents"] = amount_cents - gfe_delta if gfe_delta and amount_cents else None

    return {
        "patient": dict(case.patient),
        "bill": bill,
        "hospital": hospital_to_contract(hospital) if hospital is not None else {},
        "documents": [{"type": d.type} for d in case.documents],
    }


REAL_CASES = {case.case_id: _real_case(case) for case in CASES}


class TestOrderingAgainstTheRealCorpus:
    """§4 persona 3 WO3: debt validation sequences first when it applies."""

    def test_case_03_debt_validation_sequences_first(self):
        decisions = select_fronts(REAL_CASES["case_03_in_collections_ca"], today=TODAY)
        assert decisions[0].front == "debt_validation"
        assert decisions[0].applicable is True

    def test_only_case_03_has_an_applicable_debt_validation(self):
        for case_id, case in REAL_CASES.items():
            decisions = select_fronts(case, today=TODAY)
            dv = next(d for d in decisions if d.front == "debt_validation")
            expect_applicable = case_id == "case_03_in_collections_ca"
            assert dv.applicable is expect_applicable, case_id

    def test_other_applicable_fronts_note_the_debt_validation_sequencing(self):
        """When debt validation fires, every other applicable front's reason
        must say it is sequenced behind the dispute -- the ordering must be
        visible in the audit trail, not just in list position."""
        decisions = select_fronts(REAL_CASES["case_03_in_collections_ca"], today=TODAY)
        for d in decisions:
            if d.front == "debt_validation" or not d.applicable:
                continue
            assert "debt validation" in d.reason.lower()

    def test_every_case_returns_exactly_one_decision_per_front(self):
        for case_id, case in REAL_CASES.items():
            decisions = select_fronts(case, today=TODAY)
            assert {d.front for d in decisions} == set(FRONT_ORDER), case_id
            assert len(decisions) == len(FRONT_ORDER), case_id


class TestApplicableFrontsAlwaysHaveAMeetableDeadline:
    """§4 persona 3 WO4's other guardrail: never mark a front applicable
    without a deadline that can actually be met -- either a real due date
    that has not yet passed, or a front (like CA charity care, or audit) that
    genuinely carries no deadline at all."""

    def test_across_the_whole_corpus(self):
        for case_id, case in REAL_CASES.items():
            for d in select_fronts(case, today=TODAY):
                if not d.applicable:
                    continue
                if d.deadline is None:
                    # Only legitimate for fronts with no deadline concept
                    # (audit) or a state with no charity-care deadline (CA).
                    assert d.front in ("audit", "charity_care"), (
                        f"{case_id}/{d.front}: applicable with no deadline at all"
                    )
                    continue
                assert d.deadline >= TODAY, (
                    f"{case_id}/{d.front}: applicable with an already-expired deadline "
                    f"({d.deadline})"
                )

    def test_case_02_charity_care_deadline_is_real_and_open(self):
        """The flagship 'deadline drama' case: applicable, with days left."""
        d = next(
            x
            for x in select_fronts(REAL_CASES["case_02_wrongful_denial_il"], today=TODAY)
            if x.front == "charity_care"
        )
        assert d.applicable is True
        assert d.deadline is not None
        assert d.deadline >= TODAY


class TestPPDRAgainstTheRealCorpus:
    def test_ppdr_applicable_only_for_the_expected_two_cases(self):
        applicable = {
            case_id
            for case_id, case in REAL_CASES.items()
            if next(d for d in select_fronts(case, today=TODAY) if d.front == "ppdr").applicable
        }
        assert applicable == {"case_01_uninsured_gfe_ca", "case_07_il_concurrent_clocks"}


class TestForProfitHonesty:
    def test_case_04_forprofit_hospital_gets_no_charity_care_front(self):
        """The for-profit fixture must not get an invented 501(r) right, even
        though Illinois's separate state discount still (correctly) shows up
        as a live deadline in compute_deadlines -- select_fronts's
        charity_care bucket is specifically the federal 26 CFR 1.501(r) front."""
        d = next(
            x
            for x in select_fronts(REAL_CASES["case_04_forprofit_il"], today=TODAY)
            if x.front == "charity_care"
        )
        assert d.applicable is False
        assert "for-profit" in d.reason
        assert "1.501(r)" in d.citation


class TestAuditAgainstTheRealCorpus:
    def test_audit_applies_whenever_an_itemized_bill_document_exists(self):
        for case_id, case in REAL_CASES.items():
            has_itemized_bill_doc = any(doc["type"] == "itemized_bill" for doc in case["documents"])
            d = next(x for x in select_fronts(case, today=TODAY) if x.front == "audit")
            assert d.applicable is has_itemized_bill_doc, case_id
