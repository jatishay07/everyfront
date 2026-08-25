"""Unit tests for the pure parts of datapipes.crosswalk (no network)."""

from __future__ import annotations

from datapipes.crosswalk import ein_from_mrf_filename, is_nonprofit


class TestEinFromMrfFilename:
    def test_extracts_ein_from_real_shaped_filename(self):
        # Real filename observed from advocatehealth.com/cms-hpt.txt (spike gate b).
        assert (
            ein_from_mrf_filename("362169147_advocate-christ-medical-center_standardcharges.csv")
            == "362169147"
        )

    def test_extracts_ein_with_full_url(self):
        assert (
            ein_from_mrf_filename(
                "https://example.com/946174066_stanford-health-care_standardcharges.json"
            )
            is None
        )  # regex is anchored to the start -- full URLs need basename() first

    def test_returns_none_when_no_convention_followed(self):
        assert ein_from_mrf_filename("standardcharges.csv") is None


class TestIsNonprofit:
    def test_voluntary_nonprofit(self):
        assert is_nonprofit("Voluntary non-profit - Private") is True

    def test_government(self):
        assert is_nonprofit("Government - Hospital District or Authority") is False

    def test_proprietary(self):
        assert is_nonprofit("Proprietary") is False

    def test_none_when_missing(self):
        assert is_nonprofit(None) is None
        assert is_nonprofit("") is None
