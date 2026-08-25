"""Unit tests for datapipes.seed_cash_prices: pure logic mocked against a fake
MRF fetch and a fake Firestore client -- no network, no real GCP project."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from datapipes import seed_cash_prices


@dataclass
class _FakeCashPrice:
    code: str
    cash: float


def test_build_cash_prices_rounds_to_cents():
    with patch(
        "datapipes.seed_cash_prices.fetch_cash_prices",
        return_value=[_FakeCashPrice("86787", 70.0), _FakeCashPrice("71046", 160.5)],
    ):
        result = seed_cash_prices.build_cash_prices("http://x/mrf.csv", ["86787", "71046"])
    assert result == {"86787": 7000, "71046": 16050}


def test_build_cash_prices_empty_when_no_matches():
    with patch("datapipes.seed_cash_prices.fetch_cash_prices", return_value=[]):
        assert seed_cash_prices.build_cash_prices("http://x/mrf.csv", ["00000"]) == {}


class _FakeDocSnap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, data):
        self._data = data
        self.set_calls = []

    def get(self):
        return _FakeDocSnap(self._data)

    def set(self, payload, merge=False):
        self.set_calls.append((payload, merge))


class _FakeCollection:
    def __init__(self, doc_ref):
        self._doc_ref = doc_ref

    def document(self, ein):
        return self._doc_ref


class _FakeFirestoreClient:
    def __init__(self, doc_ref):
        self._doc_ref = doc_ref

    def collection(self, name):
        assert name == "hospitals"
        return _FakeCollection(self._doc_ref)


def test_enrich_reads_mrf_url_from_the_existing_hospital_record():
    doc_ref = _FakeDocRef({"name": "Advocate Christ Medical Center", "mrf_url": "http://x/mrf.csv"})
    with (
        patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)),
        patch(
            "datapipes.seed_cash_prices.build_cash_prices",
            return_value={"86787": 7000},
        ) as build,
    ):
        result = seed_cash_prices.enrich_hospital_cash_prices("36-2169147", ["86787"])
    build.assert_called_once_with("http://x/mrf.csv", ["86787"])
    assert result == {"86787": 7000}
    assert doc_ref.set_calls == [({"cash_prices": {"86787": 7000}}, True)]


def test_enrich_merges_never_overwrites_other_fields():
    """merge=True is the whole point -- a bad merge=False here would silently
    wipe out the hospital's fap_url etc. the next time this runs."""
    doc_ref = _FakeDocRef({"name": "X", "mrf_url": "http://x/mrf.csv"})
    with (
        patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)),
        patch("datapipes.seed_cash_prices.build_cash_prices", return_value={"A": 100}),
    ):
        seed_cash_prices.enrich_hospital_cash_prices("00-0000000", ["A"])
    payload, merge = doc_ref.set_calls[0]
    assert merge is True
    assert set(payload.keys()) == {"cash_prices"}


def test_enrich_returns_empty_and_skips_write_when_no_mrf_url():
    doc_ref = _FakeDocRef({"name": "Sutter Bay Hospitals", "mrf_url": None})
    with patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)):
        result = seed_cash_prices.enrich_hospital_cash_prices("94-0562680", ["36415"])
    assert result == {}
    assert doc_ref.set_calls == []


def test_enrich_returns_empty_and_skips_write_when_mrf_yields_nothing():
    doc_ref = _FakeDocRef({"name": "X", "mrf_url": "http://x/mrf.csv"})
    with (
        patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)),
        patch("datapipes.seed_cash_prices.build_cash_prices", return_value={}),
    ):
        result = seed_cash_prices.enrich_hospital_cash_prices("36-2169147", ["ZZZZZ"])
    assert result == {}
    assert doc_ref.set_calls == []


def test_enrich_raises_when_hospital_does_not_exist():
    doc_ref = _FakeDocRef(None)
    with patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)):
        try:
            seed_cash_prices.enrich_hospital_cash_prices("00-0000000", ["A"])
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_dry_run_fetches_but_never_writes():
    doc_ref = _FakeDocRef({"name": "X", "mrf_url": "http://x/mrf.csv"})
    with (
        patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)),
        patch("datapipes.seed_cash_prices.build_cash_prices", return_value={"A": 100}),
    ):
        result = seed_cash_prices.enrich_hospital_cash_prices("00-0000000", ["A"], dry_run=True)
    assert result == {"A": 100}
    assert doc_ref.set_calls == []


def test_mrf_url_override_skips_the_firestore_read():
    doc_ref = _FakeDocRef(None)  # would raise if ever read -- override must skip it
    with (
        patch("google.cloud.firestore.Client", return_value=_FakeFirestoreClient(doc_ref)),
        patch("datapipes.seed_cash_prices.build_cash_prices", return_value={"A": 100}) as build,
    ):
        seed_cash_prices.enrich_hospital_cash_prices(
            "00-0000000", ["A"], mrf_url_override="http://override/mrf.csv"
        )
    build.assert_called_once_with("http://override/mrf.csv", ["A"])
