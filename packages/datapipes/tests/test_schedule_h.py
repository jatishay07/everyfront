"""Unit tests for datapipes.schedule_h.

Fixture XML is a trimmed-down but structurally real 990 e-file document (same
element names/namespace/paths as a real IRS filing, verified against a real
downloaded Advocate Health return during development -- see docs/SPIKE.md).
"""

from __future__ import annotations

import pytest
from datapipes.schedule_h import classify_url, parse_return

NS = "http://www.irs.gov/efile"


def _xml(
    *,
    ein="362169147",
    state="IL",
    free="250.000000000000",
    disc="0",
    fap_url="HTTP://WWW.ADVOCATEHEALTH.COM/FINANCIALASSISTANCE",
    include_ein=True,
) -> bytes:
    ein_block = f"<EIN>{ein}</EIN>" if include_ein else ""
    return f"""<?xml version="1.0"?>
<Return xmlns="{NS}">
  <ReturnHeader>
    <TaxPeriodEndDt>2023-12-31</TaxPeriodEndDt>
    <Filer>
      {ein_block}
      <BusinessName><BusinessNameLine1Txt>TEST HEALTH CORP</BusinessNameLine1Txt></BusinessName>
      <USAddress><CityNm>OAK BROOK</CityNm>
        <StateAbbreviationCd>{state}</StateAbbreviationCd></USAddress>
    </Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990ScheduleH>
      <HospitalFcltyPoliciesPrctcGrp>
        <HospitalFacilityName>
          <BusinessNameLine1Txt>TEST HOSPITAL</BusinessNameLine1Txt></HospitalFacilityName>
        <FacilityNum>1</FacilityNum>
        <FPGFamilyIncmLmtFreeCarePct>{free}</FPGFamilyIncmLmtFreeCarePct>
        <FPGFamilyIncmLmtDscntCarePct>{disc}</FPGFamilyIncmLmtDscntCarePct>
        <FAPAvailableOnWebsiteURLTxt>{fap_url}</FAPAvailableOnWebsiteURLTxt>
      </HospitalFcltyPoliciesPrctcGrp>
    </IRS990ScheduleH>
  </ReturnData>
</Return>""".encode()


def test_parses_ein_name_state():
    org = parse_return(_xml())
    assert org.ein == "362169147"
    assert org.org_name == "TEST HEALTH CORP"
    assert org.state == "IL"
    assert org.tax_period_end == "2023-12-31"


def test_missing_ein_raises():
    with pytest.raises(ValueError, match="could not resolve filer EIN"):
        parse_return(_xml(include_ein=False))


def test_zero_sentinel_maps_to_none():
    """The core correctness trap from docs/SPIKE.md gate (a): 0 means NOT
    OFFERED, not "0% of FPL". A literal 0 must become None, never 0."""
    org = parse_return(_xml(disc="0"))
    fac = org.facilities[0]
    assert fac.discounted_care_max_fpl_pct is None
    assert any("NOT OFFERED" in q for q in fac.quirks)


def test_free_threshold_parsed_as_int():
    org = parse_return(_xml(free="250.000000000000"))
    assert org.facilities[0].free_care_max_fpl_pct == 250


def test_negative_threshold_flagged_and_ignored():
    org = parse_return(_xml(free="-5"))
    fac = org.facilities[0]
    assert fac.free_care_max_fpl_pct is None
    assert any("negative" in q for q in fac.quirks)


def test_non_numeric_threshold_flagged_not_guessed():
    org = parse_return(_xml(free="N/A"))
    fac = org.facilities[0]
    assert fac.free_care_max_fpl_pct is None
    assert any("not numeric" in q for q in fac.quirks)


def test_uppercase_url_repaired_lowercase_scheme_host():
    org = parse_return(_xml(fap_url="HTTP://WWW.ADVOCATEHEALTH.COM/FINANCIALASSISTANCE"))
    fac = org.facilities[0]
    assert fac.fap_url_status == "usable"
    assert fac.fap_url == "http://www.advocatehealth.com/FINANCIALASSISTANCE"


class TestClassifyUrl:
    def test_blank(self):
        assert classify_url(None) == ("blank", None)
        assert classify_url("   ") == ("blank", None)

    def test_cross_reference(self):
        status, url = classify_url("SEE PART V, SECTION C")
        assert status == "cross_reference"
        assert url is None

    def test_missing_colon_repaired(self):
        status, url = classify_url("HTTPS//WVUMEDICINE.ORG/FAP")
        assert status == "repaired"
        assert url == "https://wvumedicine.org/FAP"

    def test_missing_scheme_repaired(self):
        status, url = classify_url("WWW.SENTARA.COM/financial-assistance")
        assert status == "repaired"
        assert url == "http://www.sentara.com/financial-assistance"

    def test_already_usable_untouched_besides_case(self):
        status, url = classify_url("https://Example.COM/Fap")
        assert status == "usable"
        assert url == "https://example.com/Fap"  # path case preserved

    def test_unrepairable_garbage(self):
        status, url = classify_url("call the billing office")
        assert status == "unrepairable"
        assert url is None

    def test_path_case_preserved_host_lowercased(self):
        _, url = classify_url("HTTP://WWW.ADVOCATEHEALTH.COM/FinancialAssistance")
        assert url == "http://www.advocatehealth.com/FinancialAssistance"
