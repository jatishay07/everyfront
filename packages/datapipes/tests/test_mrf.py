"""Unit tests for datapipes.mrf, mocked -- no network in the default pytest run.

Live-network confirmation against real hospital systems (Advocate, Cedars-
Sinai, Stanford -- docs/SPIKE.md gate (b)) was done manually during
development; see the module docstring for what was verified and how.
"""

from __future__ import annotations

from unittest.mock import patch

from datapipes import mrf


class _FakeResp:
    def __init__(self, text="", status=200, lines=None, raw=None, headers=None):
        self.text = text
        self.status_code = status
        self._lines = lines or []
        self.raw = raw
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_cms_hpt_single_block():
    text = (
        "location-name: Advocate Christ Medical Center\n"
        "source-page-url: https://www.advocatehealth.com/fap\n"
        "mrf-url: https://example.com/362169147_advocate_standardcharges.csv\n"
        "contact-name: Standard Charges\n"
        "contact-email: sc@advocatehealth.com\n"
    )
    with patch("datapipes.mrf.requests.get", return_value=_FakeResp(text=text)):
        pointers = mrf.fetch_cms_hpt("advocatehealth.com")
    assert len(pointers) == 1
    assert pointers[0].location_name == "Advocate Christ Medical Center"
    assert pointers[0].mrf_url.endswith("standardcharges.csv")


def test_fetch_cms_hpt_multi_block():
    text = (
        "location-name: Location A\nmrf-url: http://a.example/a.json\n"
        "location-name: Location B\nmrf-url: http://a.example/b.json\n"
    )
    with patch("datapipes.mrf.requests.get", return_value=_FakeResp(text=text)):
        pointers = mrf.fetch_cms_hpt("system.example")
    assert [p.location_name for p in pointers] == ["Location A", "Location B"]


def test_fetch_cms_hpt_returns_empty_on_network_failure():
    import requests as real_requests

    with patch("datapipes.mrf.requests.get", side_effect=real_requests.Timeout()):
        assert mrf.fetch_cms_hpt("dead-domain.example") == []


def test_fetch_cms_hpt_returns_empty_on_garbage_body():
    with patch("datapipes.mrf.requests.get", return_value=_FakeResp(text="<html>404</html>")):
        assert mrf.fetch_cms_hpt("bad.example") == []


def _csv_mrf_lines():
    header_row1 = b'"attestation text",,,'
    header_row2 = b"Test Hospital,2025-01-01,3.0.0,Test Hospital,addr,123,npi,,,,"
    header_row3 = (
        b"description,code|1,code|1|type,code|2,code|2|type,code|3,code|3|type,"
        b"modifiers,setting,standard_charge|gross,standard_charge|discounted_cash"
    )
    data1 = b"Varicella IgG,86787,CPT,,,,,,,140.00,70.00"
    data2 = b"No cash price row,99999,CPT,,,,,,,500.00,"  # trap: empty cash -- must be skipped
    return [header_row1, header_row2, header_row3, data1, data2]


def test_parse_csv_mrf_filters_empty_cash_and_matches_code():
    resp = _FakeResp(lines=_csv_mrf_lines(), headers={"Content-Type": "text/csv"})
    with patch("datapipes.mrf.requests.get", return_value=resp):
        prices = mrf.fetch_cash_prices("http://example.com/x.csv", ["86787", "99999"])
    assert len(prices) == 1
    assert prices[0].code == "86787"
    assert prices[0].cash == 70.0
    assert prices[0].gross == 140.0


def test_parse_csv_mrf_no_codes_requested_present():
    resp = _FakeResp(lines=_csv_mrf_lines(), headers={"Content-Type": "text/csv"})
    with patch("datapipes.mrf.requests.get", return_value=resp):
        prices = mrf.fetch_cash_prices("http://example.com/x.csv", ["00000"])
    assert prices == []


def test_fetch_cash_prices_returns_empty_on_exception():
    with patch("datapipes.mrf.requests.get", side_effect=OSError("boom")):
        assert mrf.fetch_cash_prices("http://example.com/x.csv", ["86787"]) == []


def test_discover_and_fetch_tries_each_pointer_until_one_yields_results():
    text = (
        "location-name: A\nmrf-url: http://a.example/empty.csv\n"
        "location-name: B\nmrf-url: http://a.example/has-data.csv\n"
    )

    def fake_get(url, headers=None, timeout=None, allow_redirects=None, stream=None):
        if url == "https://system.example/cms-hpt.txt":
            return _FakeResp(text=text)
        if url == "http://a.example/empty.csv":
            return _FakeResp(
                lines=[b'"x"', b"h1,h2,h3", b"desc,00000,CPT,,,,"],
                headers={"Content-Type": "text/csv"},
            )
        if url == "http://a.example/has-data.csv":
            return _FakeResp(lines=_csv_mrf_lines(), headers={"Content-Type": "text/csv"})
        raise AssertionError(f"unexpected url {url}")

    with patch("datapipes.mrf.requests.get", side_effect=fake_get):
        used_url, prices = mrf.discover_and_fetch("system.example", ["86787"])
    assert used_url == "http://a.example/has-data.csv"
    assert len(prices) == 1
    assert prices[0].code == "86787"
