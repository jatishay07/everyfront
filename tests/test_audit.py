"""Line-item audit tests -- STATUTE (persona 3), work order 4.

Covers NCCI PTP conflicts, MUE unit ceilings, exact-duplicate lines, and the
cash-price delta -- each independently gated on whether a lookup was injected,
per the module's "LEDGER's table may not exist yet" design.

LEDGER's WO6 audit fix added two things beyond that original scope, tested
below: (1) a definite-indicator (modifier_allowed=False) PTP conflict now
carries a dollar figure -- the column-2 line's own charge, since that code
should never have been billed at all; an indicator-1 conflict still carries
none, since a modifier could make it legitimate and we have no evidence
either way; (2) `total_savings_cents`, which prevents double-counting when
two different checks name the same line with two different overcharge
theories.
"""

from __future__ import annotations

from rules.audit import AuditFinding, PTPEdit, audit_line_items, total_savings_cents


def _known_pair_lookup(a: str, b: str) -> PTPEdit | None:
    table = {("99213", "99214"): PTPEdit("99213", "99214", modifier_allowed=False)}
    return table.get((a, b))


def _no_edit_lookup(a: str, b: str) -> PTPEdit | None:
    return None


class TestPTP:
    def test_flags_a_known_column2_with_column1(self):
        items = [
            {"code": "99213", "units": 1, "charge_cents": 100_00},
            {"code": "99214", "units": 1},
        ]
        findings = audit_line_items(items, ptp_lookup=_known_pair_lookup)
        ptp = [f for f in findings if f.kind == "ptp_conflict"]
        assert len(ptp) == 1
        assert ptp[0].codes == ("99213", "99214")
        assert ptp[0].lines == (0, 1)

    def test_resolves_regardless_of_input_order(self):
        """Real NCCI tables are directional; the caller shouldn't need to pre-sort."""
        items = [{"code": "99214"}, {"code": "99213"}]
        findings = audit_line_items(items, ptp_lookup=_known_pair_lookup)
        assert len(findings) == 1

    def test_modifier_allowed_edit_gets_softer_wording(self):
        def lookup(a, b):
            return PTPEdit(a, b, modifier_allowed=True) if (a, b) == ("A", "B") else None

        items = [{"code": "A"}, {"code": "B"}]
        findings = audit_line_items(items, ptp_lookup=lookup)
        assert "modifier" in findings[0].description
        assert "may never be billed together" not in findings[0].description

    def test_modifier_disallowed_edit_says_never(self):
        def lookup(a, b):
            return PTPEdit(a, b, modifier_allowed=False) if (a, b) == ("A", "B") else None

        items = [{"code": "A"}, {"code": "B"}]
        findings = audit_line_items(items, ptp_lookup=lookup)
        assert "may never be billed together" in findings[0].description

    def test_no_edit_between_unrelated_codes_is_silent(self):
        findings = audit_line_items([{"code": "A"}, {"code": "B"}], ptp_lookup=_no_edit_lookup)
        assert not any(f.kind == "ptp_conflict" for f in findings)

    def test_identical_codes_are_not_a_ptp_conflict(self):
        calls = []

        def lookup(a, b):
            calls.append((a, b))
            return None

        findings = audit_line_items([{"code": "A"}, {"code": "A"}], ptp_lookup=lookup)
        assert not any(f.kind == "ptp_conflict" for f in findings)
        assert calls == []  # short-circuited before ever calling the lookup

    def test_no_lookup_means_no_ptp_checks_at_all(self):
        findings = audit_line_items([{"code": "99213"}, {"code": "99214"}])
        assert not any(f.kind == "ptp_conflict" for f in findings)

    def test_fewer_than_two_lines_skips_ptp_entirely(self):
        calls = []

        def lookup(a, b):
            calls.append((a, b))
            return None

        findings = audit_line_items([{"code": "99213"}], ptp_lookup=lookup)
        assert calls == []
        assert findings == []

    def test_duplicate_pairs_are_not_double_flagged(self):
        """Three lines of the same two conflicting codes -- one finding, not several."""

        def lookup(a, b):
            return PTPEdit(a, b, False) if {a, b} == {"A", "B"} else None

        items = [{"code": "A"}, {"code": "B"}, {"code": "A"}, {"code": "B"}]
        findings = audit_line_items(items, ptp_lookup=lookup)
        assert len([f for f in findings if f.kind == "ptp_conflict"]) == 1

    def test_indicator_zero_prices_the_column2_line_as_the_overcharge(self):
        """Never-billable-together: the component code should never have
        appeared at all, so its own charge is a certain overcharge."""

        def lookup(a, b):
            return (
                PTPEdit("99213", "36415", modifier_allowed=False)
                if {a, b}
                == {
                    "99213",
                    "36415",
                }
                else None
            )

        items = [
            {"code": "99213", "units": 1, "charge_cents": 10_000},
            {"code": "36415", "units": 1, "charge_cents": 3_500},
        ]
        findings = audit_line_items(items, ptp_lookup=lookup)
        ptp = [f for f in findings if f.kind == "ptp_conflict"][0]
        assert ptp.potential_savings_cents == 3_500

    def test_indicator_zero_with_no_charge_on_the_component_line_stays_unpriced(self):
        def lookup(a, b):
            return (
                PTPEdit("99213", "36415", modifier_allowed=False)
                if {a, b}
                == {
                    "99213",
                    "36415",
                }
                else None
            )

        items = [{"code": "99213", "charge_cents": 10_000}, {"code": "36415"}]
        findings = audit_line_items(items, ptp_lookup=lookup)
        ptp = [f for f in findings if f.kind == "ptp_conflict"][0]
        assert ptp.potential_savings_cents is None

    def test_indicator_one_never_carries_a_dollar_figure(self):
        """Billable together with a modifier -- without evidence one was
        used, this stays a review flag, never a priced finding."""

        def lookup(a, b):
            return PTPEdit("A", "B", modifier_allowed=True) if {a, b} == {"A", "B"} else None

        items = [
            {"code": "A", "charge_cents": 100_00},
            {"code": "B", "charge_cents": 50_00},
        ]
        findings = audit_line_items(items, ptp_lookup=lookup)
        ptp = [f for f in findings if f.kind == "ptp_conflict"][0]
        assert ptp.potential_savings_cents is None


class TestMUE:
    def test_units_over_ceiling_is_flagged(self):
        items = [{"code": "J1234", "units": 5, "charge_cents": 500}]
        findings = audit_line_items(items, mue_lookup=lambda code: 2)
        mue = [f for f in findings if f.kind == "mue_exceeded"]
        assert len(mue) == 1
        assert mue[0].potential_savings_cents == 500 - (500 * 2 // 5)

    def test_units_at_ceiling_is_not_flagged(self):
        items = [{"code": "J1234", "units": 2}]
        findings = audit_line_items(items, mue_lookup=lambda code: 2)
        assert not any(f.kind == "mue_exceeded" for f in findings)

    def test_units_under_ceiling_is_not_flagged(self):
        items = [{"code": "J1234", "units": 1}]
        findings = audit_line_items(items, mue_lookup=lambda code: 2)
        assert not any(f.kind == "mue_exceeded" for f in findings)

    def test_unknown_code_yields_no_finding(self):
        items = [{"code": "ZZZZZ", "units": 99}]
        findings = audit_line_items(items, mue_lookup=lambda code: None)
        assert not any(f.kind == "mue_exceeded" for f in findings)

    def test_no_lookup_means_no_mue_checks(self):
        items = [{"code": "J1234", "units": 99}]
        findings = audit_line_items(items)
        assert not any(f.kind == "mue_exceeded" for f in findings)

    def test_missing_charge_still_flags_but_without_savings(self):
        items = [{"code": "J1234", "units": 5}]
        findings = audit_line_items(items, mue_lookup=lambda code: 2)
        mue = [f for f in findings if f.kind == "mue_exceeded"]
        assert len(mue) == 1
        assert mue[0].potential_savings_cents is None

    def test_default_units_of_one_never_exceeds_a_ceiling_of_zero_or_more(self):
        """A line with no `units` key defaults to 1 rather than being dropped."""
        items = [{"code": "J1234"}]
        findings = audit_line_items(items, mue_lookup=lambda code: 0)
        assert any(f.kind == "mue_exceeded" for f in findings)


class TestDuplicates:
    def test_exact_duplicate_lines_are_flagged(self):
        items = [
            {"code": "80053", "units": 1, "charge_cents": 5_000},
            {"code": "80053", "units": 1, "charge_cents": 5_000},
        ]
        findings = audit_line_items(items)
        dup = [f for f in findings if f.kind == "duplicate"]
        assert len(dup) == 1
        assert dup[0].lines == (0, 1)
        assert dup[0].potential_savings_cents == 5_000

    def test_three_identical_lines_flag_once_with_all_indices(self):
        items = [{"code": "80053", "units": 1, "charge_cents": 5_000}] * 3
        findings = audit_line_items(items)
        dup = [f for f in findings if f.kind == "duplicate"]
        assert len(dup) == 1
        assert dup[0].lines == (0, 1, 2)
        assert dup[0].potential_savings_cents == 10_000

    def test_different_charge_is_not_a_duplicate(self):
        items = [
            {"code": "80053", "units": 1, "charge_cents": 5_000},
            {"code": "80053", "units": 1, "charge_cents": 6_000},
        ]
        findings = audit_line_items(items)
        assert not any(f.kind == "duplicate" for f in findings)

    def test_different_units_is_not_a_duplicate(self):
        items = [
            {"code": "80053", "units": 1, "charge_cents": 5_000},
            {"code": "80053", "units": 2, "charge_cents": 5_000},
        ]
        findings = audit_line_items(items)
        assert not any(f.kind == "duplicate" for f in findings)

    def test_single_line_is_never_a_duplicate(self):
        findings = audit_line_items([{"code": "80053"}])
        assert not any(f.kind == "duplicate" for f in findings)

    def test_duplicate_lines_missing_charge_still_flag_without_savings(self):
        items = [{"code": "80053", "units": 1}, {"code": "80053", "units": 1}]
        findings = audit_line_items(items)
        dup = [f for f in findings if f.kind == "duplicate"]
        assert len(dup) == 1
        assert dup[0].potential_savings_cents is None


class TestCashPriceDelta:
    def test_charge_above_cash_price_is_flagged(self):
        # Spike gate (b): Advocate billed $140 gross vs $70 cash for 86787.
        items = [{"code": "86787", "units": 1, "charge_cents": 140_00}]
        findings = audit_line_items(items, cash_price_lookup=lambda code: 70_00)
        cpd = [f for f in findings if f.kind == "cash_price_delta"]
        assert len(cpd) == 1
        assert cpd[0].potential_savings_cents == 70_00

    def test_charge_at_or_below_cash_price_is_not_flagged(self):
        items = [{"code": "86787", "units": 1, "charge_cents": 50_00}]
        findings = audit_line_items(items, cash_price_lookup=lambda code: 70_00)
        assert not any(f.kind == "cash_price_delta" for f in findings)

    def test_unknown_cash_price_yields_no_finding(self):
        items = [{"code": "ZZZZZ", "units": 1, "charge_cents": 100}]
        findings = audit_line_items(items, cash_price_lookup=lambda code: None)
        assert not any(f.kind == "cash_price_delta" for f in findings)

    def test_missing_charge_is_skipped_gracefully(self):
        items = [{"code": "86787", "units": 1}]
        findings = audit_line_items(items, cash_price_lookup=lambda code: 70_00)
        assert not any(f.kind == "cash_price_delta" for f in findings)

    def test_no_lookup_means_no_cash_price_checks(self):
        items = [{"code": "86787", "units": 1, "charge_cents": 140_00}]
        findings = audit_line_items(items)
        assert not any(f.kind == "cash_price_delta" for f in findings)

    def test_multi_unit_delta_scales_with_units(self):
        items = [{"code": "86787", "units": 3, "charge_cents": 420_00}]  # $140/unit vs $70/unit
        findings = audit_line_items(items, cash_price_lookup=lambda code: 70_00)
        cpd = [f for f in findings if f.kind == "cash_price_delta"]
        assert cpd[0].potential_savings_cents == 210_00


class TestGracefulDegradation:
    def test_empty_items_yields_no_findings(self):
        assert audit_line_items([]) == []

    def test_none_items_is_treated_as_empty(self):
        assert audit_line_items(None) == []

    def test_non_dict_item_is_skipped(self):
        findings = audit_line_items(["not a dict", {"code": "A"}])
        assert findings == []

    def test_missing_code_is_skipped(self):
        findings = audit_line_items([{"units": 1, "charge_cents": 100}])
        assert findings == []

    def test_blank_code_is_skipped(self):
        findings = audit_line_items([{"code": "   "}])
        assert findings == []

    def test_non_string_code_is_skipped(self):
        findings = audit_line_items([{"code": 12345}])
        assert findings == []

    def test_negative_charge_is_treated_as_absent(self):
        items = [{"code": "86787", "units": 1, "charge_cents": -100}]
        findings = audit_line_items(items, cash_price_lookup=lambda code: 70_00)
        assert not any(f.kind == "cash_price_delta" for f in findings)

    def test_zero_units_falls_back_to_one(self):
        items = [{"code": "J1234", "units": 0}]
        findings = audit_line_items(items, mue_lookup=lambda code: 0)
        assert any(f.kind == "mue_exceeded" for f in findings)

    def test_bool_is_not_treated_as_a_valid_unit_count(self):
        items = [{"code": "J1234", "units": True}]
        findings = audit_line_items(items, mue_lookup=lambda code: 0)
        # units=True is rejected (not a plain int) and falls back to the default of 1,
        # which still exceeds a ceiling of 0.
        assert any(f.kind == "mue_exceeded" for f in findings)


class TestExplain:
    def test_explain_includes_savings_when_present(self):
        finding = AuditFinding("duplicate", ("A",), "desc", "cite", potential_savings_cents=500)
        assert "Potential overcharge" in finding.explain()
        assert "$5.00" in finding.explain()

    def test_explain_omits_savings_when_absent(self):
        finding = AuditFinding("duplicate", ("A",), "desc", "cite")
        assert "Potential overcharge" not in finding.explain()

    def test_explain_omits_savings_when_zero(self):
        finding = AuditFinding("duplicate", ("A",), "desc", "cite", potential_savings_cents=0)
        assert "Potential overcharge" not in finding.explain()


class TestTotalSavingsCents:
    def test_disjoint_findings_simply_sum(self):
        findings = [
            AuditFinding("duplicate", ("A",), "d", "c", lines=(0, 1), potential_savings_cents=500),
            AuditFinding(
                "cash_price_delta", ("B",), "d", "c", lines=(2,), potential_savings_cents=300
            ),
        ]
        assert total_savings_cents(findings) == 800

    def test_overlapping_findings_on_the_same_line_take_the_max_not_the_sum(self):
        """Real shape from case_07: a duplicate metabolic-panel pair (lines
        3,4) AND a per-line cash-price delta on each of those same two lines
        -- two theories about the same overcharge must not stack."""
        findings = [
            AuditFinding(
                "duplicate", ("80053",), "d", "c", lines=(3, 4), potential_savings_cents=22_000
            ),
            AuditFinding(
                "cash_price_delta",
                ("80053",),
                "d",
                "c",
                lines=(3,),
                potential_savings_cents=11_250,
            ),
            AuditFinding(
                "cash_price_delta",
                ("80053",),
                "d",
                "c",
                lines=(4,),
                potential_savings_cents=11_250,
            ),
        ]
        # duplicate's $220 splits into $110/line; cash-price's $112.50/line
        # wins on each line (larger) -> $225 total, not $220 + $225 = $445.
        assert total_savings_cents(findings) == 22_500

    def test_findings_with_no_savings_or_no_lines_are_ignored(self):
        findings = [
            AuditFinding("ptp_conflict", ("A", "B"), "d", "c", lines=(0, 1)),
            AuditFinding("duplicate", ("A",), "d", "c", potential_savings_cents=100),
        ]
        assert total_savings_cents(findings) == 0

    def test_empty_findings_list_is_zero(self):
        assert total_savings_cents([]) == 0

    def test_single_finding_on_a_single_line(self):
        findings = [
            AuditFinding(
                "cash_price_delta", ("A",), "d", "c", lines=(0,), potential_savings_cents=7_000
            )
        ]
        assert total_savings_cents(findings) == 7_000
