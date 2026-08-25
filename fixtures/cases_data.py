"""Synthetic patient corpus -- source of truth for the fixture bundle.

PROOF (persona 7), work orders 1-2. Every patient name, income figure, and
billing narrative in this module is FICTIONAL -- rule 0.6 / CLAUDE.md: never a
real patient name, SSN, or real patient bill.

Hospital NAME + EIN pairs for the three non-synthetic hospitals below are real
public record (IRS Schedule H Part V Section B filings, and/or the CMS
price-transparency `cms-hpt.txt` -> MRF crosswalk), verified in docs/SPIKE.md
and docs/spike/evidence_*.{json,txt}. Every field on a real hospital is tagged
with exactly how it was verified -- "schedule_h" (parsed from the real XML),
"mrf_filename_crosswalk" (EIN confirmed via the CMS-mandated MRF filename
pattern, but FAP thresholds not independently parsed), or an explicit estimate
with a citation to the statutory floor it falls back to. Nothing here invents
a fact SPIKE.md did not verify -- see each Hospital's `verification_note`,
matching the "never guess, return None" ethos of packages/rules/rules/
deadlines.py and eligibility.py.

The fourth hospital, "Prairie Crossing", is entirely synthetic and marked as
such everywhere (name, EIN, ccn) -- it exists only to give WO1's "for-profit
hospital -> honest no-501(r)-obligation path" case a hospital record, since
none of the three real seed hospitals is for-profit.

State fixture rule (BUILD_PLAYBOOK.md §2.6): every case lives in CA (no
charity-care deadline -- the safe case) or IL (90-day state clock -- the
dramatic case). No other state appears in this corpus.

`generate.py` renders this module into:
  * fixtures/generated/hospitals.json                    (hospitals/{ein} shape, §3.1)
  * fixtures/generated/cases/<case_id>/case.json          (patient/bill + expected, §3.1)
  * fixtures/generated/cases/<case_id>/documents/*        (reportlab/PIL rendered docs)
  * fixtures/generated/expected_stats.json                (the §3.4 demo stat object)

Nothing here calls a model. Where this module encodes what
`select_fronts`/`audit_line_items`/`check_denial_lawfulness` (contract §3.5)
SHOULD say, it goes through `fixtures/reference_model.py`, which is explicitly
a stand-in for STATUTE's not-yet-landed work orders 3-5 -- see that module's
docstring for the HANDOFF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

WATERMARK = "SYNTHETIC — DEMO"

# ---------------------------------------------------------------------------
# Hospitals -- contract §3.1 `hospitals/{ein}`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineItem:
    code: str
    description: str
    units: int
    unit_charge_cents: int
    # Set when this line is a deliberately-seeded audit finding:
    # "exact_duplicate" | "ptp_unbundling" | "mue_excess" | "cash_price_delta"
    finding: str | None = None
    finding_note: str = ""
    finding_amount_cents: int = 0

    @property
    def total_cents(self) -> int:
        return self.units * self.unit_charge_cents


@dataclass(frozen=True)
class Hospital:
    ein: str  # NN-NNNNNNN, contract key
    name: str
    state: str
    nonprofit: bool
    free_care_max_fpl_pct: int | None
    discounted_care_max_fpl_pct: int | None
    source: str
    tax_year: int | None
    fap_url: str | None = None
    fap_app_url: str | None = None
    mrf_url: str | None = None
    ccn: str | None = None
    verification_note: str = ""


HOSPITALS: dict[str, Hospital] = {
    # Real. EIN + Schedule H thresholds parsed directly from IRS e-file XML
    # (docs/SPIKE.md gate (a)); fap_url/mrf_url are the real, live-checked
    # URLs recorded in docs/spike/evidence_schedule_h_extract.json and
    # docs/spike/evidence_cms_hpt_advocate.txt. The raw 16a value was
    # ALL-CAPS with no scheme repair applied in the evidence file
    # ("HTTP://WWW.ADVOCATEHEALTH.COM/FINANCIALASSISTANCE"); this record
    # applies the lowercase-scheme-and-host repair SPIKE gate (a) quirk #4
    # calls for.
    "36-2169147": Hospital(
        ein="36-2169147",
        name="Advocate Christ Medical Center",
        state="IL",
        nonprofit=True,
        free_care_max_fpl_pct=250,
        discounted_care_max_fpl_pct=600,
        source="schedule_h",
        tax_year=2023,
        fap_url="https://www.advocatehealth.com/financialassistance",
        fap_app_url="https://www.advocatehealth.com/financialassistance",
        mrf_url=(
            "https://sthpiprd.blob.core.windows.net/machine-readable-files/"
            "11263/362169147_advocate-christ-medical-center_standardcharges.csv"
        ),
        verification_note=(
            "Real. Threshold + tax_period_end from Schedule H Part V Sec B "
            "(evidence_schedule_h_extract.json); fap_url + mrf_url from the "
            "live cms-hpt.txt crosswalk (evidence_cms_hpt_advocate.txt). CCN "
            "not yet resolved -- LEDGER WO2 crosswalk not built."
        ),
    ),
    # Real. Schedule H reports discounted-care FPL% as the literal integer 0,
    # which is the "not offered" sentinel (SPIKE gate (a); eligibility.py
    # NOT_OFFERED_SENTINEL) -- carried through here UNCHANGED. Do not "fix"
    # this to None in the fixture; the whole point is that screen_eligibility
    # must be the thing that interprets it, not the data.
    "94-0562680": Hospital(
        ein="94-0562680",
        name="Sutter Bay Hospitals",
        state="CA",
        nonprofit=True,
        free_care_max_fpl_pct=400,
        discounted_care_max_fpl_pct=0,
        source="schedule_h",
        tax_year=2023,
        fap_url=None,
        fap_app_url=None,
        mrf_url=None,
        verification_note=(
            "Real threshold from Schedule H. 16a was 'SEE PART V, SECTION C' "
            "(SPIKE gate (a) quirk #2) -- not a directly usable URL, so "
            "fap_url is honestly None rather than a guess. sutterhealth.org "
            "served cms-hpt.txt (200) but no MRF cash price was extracted in "
            "the spike, so mrf_url is None too."
        ),
    ),
    # Real hospital, real EIN (confirmed via the CMS-mandated MRF filename
    # pattern in SPIKE gate (b): 946174066_stanford-health-care_...), but its
    # Schedule H Part V Sec B was NOT part of the 3-filing gate-(a) sample --
    # so its FAP thresholds are NOT independently verified. Rather than
    # invent a number, this fixture falls back to the CA statutory floor
    # (Cal. HSC §127405, applies to every hospital in the state) and says so.
    "94-6174066": Hospital(
        ein="94-6174066",
        name="Stanford Health Care",
        state="CA",
        nonprofit=True,
        free_care_max_fpl_pct=400,
        discounted_care_max_fpl_pct=400,
        source="estimated_ca_statutory_floor",
        tax_year=None,
        fap_url=None,
        fap_app_url=None,
        mrf_url=None,
        verification_note=(
            "EIN + name real (MRF filename crosswalk, SPIKE gate (b): a 154 MB "
            "JSON MRF answered but was not fully parsed for cash prices). "
            "Schedule H thresholds NOT parsed for this facility -- this "
            "fixture uses the CA HSC §127405 400%/400% floor as a documented "
            "placeholder, not a filed number. Flag for LEDGER before using "
            "this EIN's thresholds outside fixtures/."
        ),
    ),
    # Entirely synthetic. No real for-profit hospital was verified in
    # docs/SPIKE.md, and WO1 needs one to exercise the honest "no 501(r)
    # obligation" path -- so this hospital, its EIN, and its CCN are all
    # fictional. The EIN prefix 00- cannot appear on a real IRS-issued EIN,
    # by design, so it can never be mistaken for a real one.
    "00-0000001": Hospital(
        ein="00-0000001",
        name="Prairie Crossing Medical Center (FOR-PROFIT) — SYNTHETIC FIXTURE",
        state="IL",
        nonprofit=False,
        free_care_max_fpl_pct=None,
        discounted_care_max_fpl_pct=None,
        source="synthetic_fixture",
        tax_year=None,
        fap_url=None,
        fap_app_url=None,
        mrf_url=None,
        verification_note=(
            "SYNTHETIC. Entirely fictional hospital -- no 26 CFR 1.501(r) "
            "duty exists (for-profit), so it files no Schedule H and has no "
            "FAP. It still sits inside Illinois, so 210 ILCS 89/10 (Hospital "
            "Uninsured Patient Discount Act) binds it regardless."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Cases -- contract §3.1 `cases/{case_id}` (patient + bill), plus a `documents`
# manifest describing what generate.py should render, plus an `expected`
# block used by tests/ to check the corpus against itself and against the
# rules that already exist.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentSpec:
    doc_id: str
    type: str  # contract §3.1 documents.type enum
    render: str  # which generate.py renderer to call
    kwargs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CaseFixture:
    case_id: str
    title: str
    proves: str
    patient: dict
    bill: dict
    line_items: tuple[LineItem, ...]
    documents: tuple[DocumentSpec, ...]
    # Denial-triage inputs, only populated for cases with a denial letter.
    denial_demanded_docs: tuple[str, ...] = ()
    denial_fap_published_docs: tuple[str, ...] = ()
    notes: str = ""


def _cents(dollars: float) -> int:
    """Dollars -> integer cents, refusing to silently round a bad literal."""
    c = round(dollars * 100)
    if abs(c - dollars * 100) > 1e-6:
        raise ValueError(f"{dollars} is not a whole cent amount")
    return c


CASES: list[CaseFixture] = [
    # ------------------------------------------------------------------
    # 1. Uninsured + GFE + California -> PPDR + charity care (free tier).
    #    The "safe" state: no charity-care application deadline exists.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_01_uninsured_gfe_ca",
        title="Uninsured self-pay, Good Faith Estimate blown, California",
        proves=(
            "PPDR (bill exceeds GFE by >= $400) stacked with charity-care "
            "free-tier eligibility, in the state with NO charity-care "
            "deadline (Cal. HSC §127405(e)(3)) -- the demo's 'safe' case."
        ),
        patient={
            "name": "Jordan Alvarez",
            "household_size": 3,
            "annual_income_cents": _cents(32_000),
            "insured": False,
            "state": "CA",
        },
        bill={
            "hospital_ein": "94-0562680",
            "provider_name": "Sutter Bay Hospitals",
            "service_date": date(2026, 5, 1),
            "first_statement_date": date(2026, 6, 5),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99284", "EMERGENCY DEPT VISIT, HIGH COMPLEXITY", 1, _cents(1_850)),
            LineItem("71046", "CHEST X-RAY, 2 VIEWS", 1, _cents(320)),
            LineItem("80053", "COMPREHENSIVE METABOLIC PANEL", 1, _cents(210)),
            LineItem(
                "80053",
                "COMPREHENSIVE METABOLIC PANEL",
                1,
                _cents(210),
                finding="exact_duplicate",
                finding_note=(
                    "Identical code, units, and charge billed twice in the same encounter."
                ),
                finding_amount_cents=_cents(210),
            ),
            LineItem(
                "36415",
                "COLLECTION OF VENOUS BLOOD BY VENIPUNCTURE",
                1,
                _cents(35),
                finding="ptp_unbundling",
                finding_note=(
                    "36415 billed alongside same-encounter E/M 99284 with no "
                    "modifier -- a commonly bundled NCCI column-2 code. "
                    "Verify against LEDGER's live NCCI PTP table (packages/"
                    "datapipes, WO3, not yet built) before filing."
                ),
                finding_amount_cents=_cents(35),
            ),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec("gfe", "gfe", "gfe_pdf", kwargs={"gfe_delta_cents": _cents(700)}),
            DocumentSpec("income_proof", "income_proof", "pay_stub_pdf"),
        ),
        notes=(
            "GFE set $700 under the final bill -- comfortably clears the "
            "45 CFR 149.620(b) $400 'substantially in excess' floor."
        ),
    ),
    # ------------------------------------------------------------------
    # 2. Insured + wrongful denial + Illinois -> deadline drama +
    #    unlawful-denial flag (26 CFR 1.501(r)-4(b)(3)).
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_02_wrongful_denial_il",
        title="Insured patient, wrongfully denied charity care, Illinois",
        proves=(
            "The federal 240-day FAP window running down (deadline drama) "
            "plus 26 CFR 1.501(r)-4(b)(3): the hospital demanded documents "
            "its own published FAP does not list, AND the patient's income "
            "is objectively under the free-care threshold -- a clean "
            "unlawful-denial flag. Patient is insured, so IL's 90-day "
            "uninsured-only state discount correctly does NOT fire here."
        ),
        patient={
            "name": "Priya Nandakumar",
            "household_size": 2,
            "annual_income_cents": _cents(40_000),
            "insured": True,
            "state": "IL",
        },
        bill={
            "hospital_ein": "36-2169147",
            "provider_name": "Advocate Christ Medical Center",
            "service_date": date(2025, 12, 20),
            "first_statement_date": date(2026, 1, 5),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99285", "EMERGENCY DEPT VISIT, HIGH COMPLEXITY", 1, _cents(2_400)),
            LineItem("96365", "IV INFUSION, INITIAL HOUR", 1, _cents(180)),
            LineItem(
                "96365",
                "IV INFUSION, INITIAL HOUR",
                1,
                _cents(180),
                finding="exact_duplicate",
                finding_note="Identical infusion line billed twice.",
                finding_amount_cents=_cents(180),
            ),
            LineItem("80048", "BASIC METABOLIC PANEL", 1, _cents(150)),
            LineItem(
                "36415",
                "COLLECTION OF VENOUS BLOOD BY VENIPUNCTURE",
                1,
                _cents(35),
                finding="ptp_unbundling",
                finding_note="Same pattern as case 1 -- 36415 alongside same-day E/M 99285.",
                finding_amount_cents=_cents(35),
            ),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec(
                "denial_letter",
                "denial_letter",
                "denial_letter_pdf",
                kwargs={"lawful": False},
            ),
        ),
        denial_demanded_docs=(
            "completed_application_form",
            "proof_of_income_last_30_days",
            "notarized_affidavit_of_indigency",
            "three_years_federal_tax_returns",
        ),
        denial_fap_published_docs=(
            "completed_application_form",
            "proof_of_income_last_30_days",
        ),
        notes=(
            "As of currentDate 2026-08-25 the federal 240-day window "
            "(due 2026-09-02) is inside its final ~8 days -- a red-chip, "
            "close-to-≤7-day deadline for the demo. Income sits at ~184.8% "
            "FPL, under Advocate's real filed 250% free-care line -- the "
            "denial was wrong on the merits AND on the documentation."
        ),
    ),
    # ------------------------------------------------------------------
    # 3. In collections -> debt-validation-first ordering.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_03_in_collections_ca",
        title="Uninsured, referred to collections, California",
        proves=(
            "in_collections + an open validation window forces "
            "debt-validation to sequence FIRST (12 CFR 1006.34 / 15 USC "
            "1692g), ahead of charity-care exploration, even in CA where "
            "the charity-care clock never runs out."
        ),
        patient={
            "name": "Denise Okafor",
            "household_size": 1,
            "annual_income_cents": _cents(18_000),
            "insured": False,
            "state": "CA",
        },
        bill={
            "hospital_ein": "94-6174066",
            "provider_name": "Stanford Health Care",
            "service_date": date(2026, 2, 20),
            "first_statement_date": date(2026, 3, 10),
            "in_collections": True,
            "collector_name": "Cascade Debt Recovery Group (SYNTHETIC)",
            "validation_notice_date": date(2026, 8, 5),
        },
        line_items=(
            LineItem("99283", "EMERGENCY DEPT VISIT, MODERATE COMPLEXITY", 1, _cents(980)),
            LineItem("73610", "X-RAY, ANKLE, 3+ VIEWS", 1, _cents(150)),
            LineItem(
                "73610",
                "X-RAY, ANKLE, 3+ VIEWS",
                1,
                _cents(150),
                finding="exact_duplicate",
                finding_note="Same ankle X-ray billed twice.",
                finding_amount_cents=_cents(150),
            ),
            LineItem("29405", "APPLICATION OF SHORT LEG SPLINT", 1, _cents(95)),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec("collection_notice", "collection_notice", "collection_notice_pdf"),
        ),
        notes=(
            "validation_notice_date is 20 days before currentDate 2026-08-25 "
            "-- the 30-day validation window (due 2026-09-04) is open but "
            "closing, which is exactly the urgency that must sequence ahead "
            "of the (in this state, deadline-free) charity-care screen."
        ),
    ),
    # ------------------------------------------------------------------
    # 4. For-profit hospital -> the honest "no 501(r) obligation" path,
    #    with the nuance that Illinois's state discount act still binds
    #    for-profit hospitals.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_04_forprofit_il",
        title="Uninsured patient at a for-profit hospital, Illinois",
        proves=(
            "Honesty over blanket relief: this hospital owes NO 26 CFR "
            "1.501(r) duty (for-profit, no Schedule H, no FAP) -- the "
            "system must say so plainly rather than inventing a charity-care "
            "front. It should still surface the Illinois 210 ILCS 89/10 "
            "uninsured discount, which binds every IL hospital regardless "
            "of tax status."
        ),
        patient={
            "name": "Marcus Whitfield",
            "household_size": 2,
            "annual_income_cents": _cents(50_000),
            "insured": False,
            "state": "IL",
        },
        bill={
            "hospital_ein": "00-0000001",
            "provider_name": "Prairie Crossing Medical Center (FOR-PROFIT) — SYNTHETIC FIXTURE",
            "service_date": date(2026, 5, 30),
            "first_statement_date": date(2026, 6, 20),
            "discharge_date": date(2026, 6, 1),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99282", "EMERGENCY DEPT VISIT, LOW COMPLEXITY", 1, _cents(650)),
            LineItem("80048", "BASIC METABOLIC PANEL", 1, _cents(150)),
            LineItem(
                "80048",
                "BASIC METABOLIC PANEL",
                1,
                _cents(150),
                finding="exact_duplicate",
                finding_note="Same metabolic panel billed twice.",
                finding_amount_cents=_cents(150),
            ),
        ),
        documents=(DocumentSpec("bill", "itemized_bill", "bill_pdf"),),
        notes=(
            "Income is ~231% of FPL for a household of 2 -- at or under the "
            "IL statutory 300% discount floor (210 ILCS 89/10; eligibility.py "
            "STATE_FLOORS['IL'] applies to for-profits), so screen_eligibility "
            "correctly returns 'discounted' via the state floor alone even "
            "though hospital.nonprofit is False and the hospital publishes no "
            "threshold of its own. discharge_date is included because IL's "
            "state discount clock runs from the latest of discharge/service/"
            "screening/public-program-denial, not from the billing statement."
        ),
    ),
    # ------------------------------------------------------------------
    # 5. A cat photo uploaded as income proof -> the Verifier's rejection
    #    case.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_05_cat_photo_income_proof",
        title="Cat photo uploaded as income proof, California",
        proves=(
            "The Verifier's 'is this document even an income proof' check "
            "(persona 5 WO1) -- the uploaded file is a synthetic cartoon cat "
            "drawing, not a pay stub or tax document. Filing must block on "
            "this with a human-readable reason rather than silently "
            "screening on no income evidence at all."
        ),
        patient={
            "name": "Sam Whitaker",
            "household_size": 4,
            "annual_income_cents": _cents(35_000),
            "insured": False,
            "state": "CA",
        },
        bill={
            "hospital_ein": "94-0562680",
            "provider_name": "Sutter Bay Hospitals",
            "service_date": date(2026, 7, 1),
            "first_statement_date": date(2026, 7, 28),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99283", "EMERGENCY DEPT VISIT, MODERATE COMPLEXITY", 1, _cents(900)),
            LineItem("85025", "COMPLETE BLOOD COUNT, AUTOMATED", 1, _cents(65)),
            LineItem(
                "85025",
                "COMPLETE BLOOD COUNT, AUTOMATED",
                1,
                _cents(65),
                finding="exact_duplicate",
                finding_note="Same CBC billed twice.",
                finding_amount_cents=_cents(65),
            ),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec("income_proof", "income_proof", "cat_photo_png"),
        ),
        notes=(
            "The math would clear free-tier eligibility (~106.5% FPL under "
            "Sutter's real 400% line) if the proof were valid -- the point "
            "of this case is entirely that it is not."
        ),
    ),
    # ------------------------------------------------------------------
    # 6. An unparseable bill -> graceful degradation.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_06_unparseable_bill",
        title="Corrupted / unreadable bill upload, California",
        proves=(
            "Graceful degradation: the uploaded file is not a valid PDF at "
            "all. The Reader must not crash, and must not invent a hospital, "
            "an amount, or a date it cannot actually read -- exactly the "
            "same 'never guess, return None' discipline as "
            "packages/rules/rules/deadlines.py's missing-date handling, "
            "just one layer up at extraction time."
        ),
        patient={
            "name": "Casey Nguyen",
            "household_size": 2,
            "annual_income_cents": _cents(26_000),
            "insured": False,
            "state": "CA",
        },
        bill={
            # Everything below is what the PATIENT self-reported on the
            # intake form -- NOT extracted from the document, which cannot
            # be parsed. hospital_ein/amount/dates are honestly None: no
            # document evidence backs them.
            "hospital_ein": None,
            "provider_name": "unknown (patient says 'a Sutter hospital')",
            "service_date": None,
            "first_statement_date": None,
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(),  # nothing to extract -- that's the point
        documents=(DocumentSpec("bill", "itemized_bill", "corrupted_bill_pdf"),),
        notes=(
            "This fixture's document is deliberately not a valid PDF (a "
            "truncated byte stream inside a plausible-looking header). "
            "Any extractor must fail closed: no hospital_ein, no amount, no "
            "dates invented from nothing."
        ),
    ),
    # ------------------------------------------------------------------
    # 7. Flagship: several statutory clocks ticking at once, Illinois.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_07_il_concurrent_clocks",
        title="Uninsured, GFE blown, Illinois — four clocks at once",
        proves=(
            "The product's actual thesis (README/video: 'five legal clocks "
            "running at once, interacting'): this single case has FOUR live "
            "Deadline objects on different clocks -- the federal 240-day FAP "
            "window, the 120-day ECA moratorium, the 120-day PPDR window, "
            "and IL's 90-day state discount -- each with a different basis "
            "date and a different due date. It also carries the richest "
            "audit trail in the corpus: a real cash-price delta from "
            "Advocate's own attested MRF (SPIKE gate (b)), an MUE-style unit "
            "excess, an exact duplicate, and an NCCI-style unbundling."
        ),
        patient={
            "name": "Aisha Bello",
            "household_size": 3,
            "annual_income_cents": _cents(30_000),
            "insured": False,
            "state": "IL",
        },
        bill={
            "hospital_ein": "36-2169147",
            "provider_name": "Advocate Christ Medical Center",
            "service_date": date(2026, 7, 9),
            "first_statement_date": date(2026, 7, 20),
            "discharge_date": date(2026, 7, 10),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99285", "EMERGENCY DEPT VISIT, HIGH COMPLEXITY", 1, _cents(2_600)),
            LineItem(
                "86787",
                "AB, VARICELLA ZOSTER IGG",
                1,
                _cents(140),
                finding="cash_price_delta",
                finding_note=(
                    "REAL figures from docs/SPIKE.md gate (b): Advocate's own "
                    "attested MRF lists code 86787 gross $140.00 / cash "
                    "$70.00 -- a flat 50%-of-gross discount. Billing a "
                    "self-pay patient the gross price is a 2x overcharge "
                    "against the hospital's own published cash price."
                ),
                finding_amount_cents=_cents(70),
            ),
            LineItem(
                "71046",
                "CHEST X-RAY, 2 VIEWS",
                3,
                _cents(95),
                finding="mue_excess",
                finding_note=(
                    "3 units billed in one line for a code with an "
                    "illustrative 1-unit/day MUE ceiling -- flagged as 2 "
                    "excess units. NOT cross-checked against a live CMS NCCI "
                    "MUE table (packages/datapipes has no NCCI pipeline yet, "
                    "WO3); verify before citing in a real filing."
                ),
                finding_amount_cents=_cents(95 * 2),
            ),
            LineItem("80053", "COMPREHENSIVE METABOLIC PANEL", 1, _cents(220)),
            LineItem(
                "80053",
                "COMPREHENSIVE METABOLIC PANEL",
                1,
                _cents(220),
                finding="exact_duplicate",
                finding_note="Same metabolic panel billed twice.",
                finding_amount_cents=_cents(220),
            ),
            LineItem(
                "36415",
                "COLLECTION OF VENOUS BLOOD BY VENIPUNCTURE",
                1,
                _cents(35),
                finding="ptp_unbundling",
                finding_note="36415 alongside same-day E/M 99285, no modifier.",
                finding_amount_cents=_cents(35),
            ),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec("gfe", "gfe", "gfe_pdf", kwargs={"gfe_delta_cents": _cents(900)}),
            DocumentSpec("income_proof", "income_proof", "pay_stub_pdf"),
        ),
        notes=(
            "discharge_date 2026-07-10, first_statement_date 2026-07-20. "
            "Relative to currentDate 2026-08-25: IL discount due 2026-10-08 "
            "(nearest), ECA moratorium + PPDR both due 2026-11-17 (same "
            "120-day offset from the same basis date -- a real coincidence "
            "of the rules, not a fixture error), federal FAP due 2027-03-17 "
            "(furthest out). Income ~109.8% FPL clears Advocate's real 250% "
            "free line."
        ),
    ),
    # ------------------------------------------------------------------
    # 8. Lawful denial -- the negative-branch contrast to case 2.
    # ------------------------------------------------------------------
    CaseFixture(
        case_id="case_08_lawful_denial_ca",
        title="Insured patient, LAWFULLY denied charity care, California",
        proves=(
            "check_denial_lawfulness's negative branch: the hospital demanded "
            "exactly the documents its own published FAP lists, and the "
            "patient's income is genuinely over the threshold on the merits. "
            "No flag should fire -- the system must be precise, not "
            "reflexive, about calling a denial unlawful."
        ),
        patient={
            "name": "Harold Kim",
            "household_size": 1,
            "annual_income_cents": _cents(70_000),
            "insured": True,
            "state": "CA",
        },
        bill={
            "hospital_ein": "94-6174066",
            "provider_name": "Stanford Health Care",
            "service_date": date(2026, 3, 15),
            "first_statement_date": date(2026, 4, 1),
            "in_collections": False,
            "collector_name": None,
            "validation_notice_date": None,
        },
        line_items=(
            LineItem("99283", "EMERGENCY DEPT VISIT, MODERATE COMPLEXITY", 1, _cents(1_100)),
            LineItem("80048", "BASIC METABOLIC PANEL", 1, _cents(140)),
            LineItem(
                "80048",
                "BASIC METABOLIC PANEL",
                1,
                _cents(140),
                finding="exact_duplicate",
                finding_note="Same metabolic panel billed twice.",
                finding_amount_cents=_cents(140),
            ),
        ),
        documents=(
            DocumentSpec("bill", "itemized_bill", "bill_pdf"),
            DocumentSpec(
                "denial_letter",
                "denial_letter",
                "denial_letter_pdf",
                kwargs={"lawful": True},
            ),
        ),
        denial_demanded_docs=(
            "completed_application_form",
            "proof_of_income_last_30_days",
        ),
        denial_fap_published_docs=(
            "completed_application_form",
            "proof_of_income_last_30_days",
        ),
        notes=(
            "Income is ~438.6% FPL (household of 1), over Stanford's "
            "estimated 400% CA-floor line -- ineligible on the merits, and "
            "the demanded-docs list exactly matches the FAP's published "
            "list. Audit front still applies (itemized bill always "
            "triggers it) even though charity care correctly does not."
        ),
    ),
]


CASES_BY_ID: dict[str, CaseFixture] = {c.case_id: c for c in CASES}

assert len(CASES) == 8, "corpus must be exactly 8 cases per playbook §4 persona 7 WO1"
assert len(CASES_BY_ID) == 8, "case_id collision in the corpus"
assert {c.patient["state"] for c in CASES} <= {"CA", "IL"}, (
    "state fixture rule (§2.6): every case must live in CA or IL"
)
