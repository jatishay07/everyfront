"""api_core.demo_fixtures -- PROOF's real corpus + the built-in fallbacks."""

from __future__ import annotations

from api_core import demo_fixtures


def test_builtin_fixture_has_documents_list():
    fixture = demo_fixtures.load_fixture("maria_uninsured_ca")
    assert fixture is not None
    assert fixture["patient"]["state"] == "CA"
    assert len(fixture["documents"]) == 1
    assert fixture["documents"][0]["type"] == "itemized_bill"


def test_unknown_fixture_returns_none():
    assert demo_fixtures.load_fixture("does-not-exist") is None


def test_available_fixtures_includes_builtins():
    names = demo_fixtures.available_fixtures()
    assert "maria_uninsured_ca" in names


def test_proof_corpus_loads_if_present():
    """Skips its assertions gracefully if fixtures/generated hasn't been
    built in this checkout (python -m fixtures.generate) -- this is an
    integration nicety, not something SWARM's own tests should hard-require
    from PROOF's generated output.
    """
    fixture = demo_fixtures.load_fixture("case_01_uninsured_gfe_ca")
    if fixture is None:
        return
    assert fixture["patient"]["state"] == "CA"
    assert fixture["patient"]["insured"] is False
    # PROOF's corpus predates annual_income -> annual_income_cents; both keys
    # must carry the same (already-cents) value.
    assert fixture["patient"]["annual_income_cents"] == fixture["patient"]["annual_income"]
    assert fixture["bill"]["hospital_ein"] == "94-0562680"
    assert fixture["hospital"]["name"] == "Sutter Bay Hospitals"
    doc_types = [d["type"] for d in fixture["documents"]]
    assert doc_types == ["itemized_bill", "gfe", "income_proof"]
    for doc in fixture["documents"]:
        assert doc["raw_text"]  # never empty


def test_proof_corpus_cat_photo_case_describes_image_not_income(monkeypatch=None):
    fixture = demo_fixtures.load_fixture("case_05_cat_photo_income_proof")
    if fixture is None:
        return
    income_doc = next(d for d in fixture["documents"] if d["type"] == "income_proof")
    assert "cat" in income_doc["raw_text"].lower()


def test_proof_corpus_unparseable_bill_is_not_real_bill_text():
    fixture = demo_fixtures.load_fixture("case_06_unparseable_bill")
    if fixture is None:
        return
    bill_doc = next(d for d in fixture["documents"] if d["type"] == "itemized_bill")
    assert "CORRUPTED" in bill_doc["raw_text"]


def test_proof_corpus_denial_hospitals_get_fap_required_documents():
    fixture = demo_fixtures.load_fixture("case_02_wrongful_denial_il")
    if fixture is None:
        return
    assert fixture["hospital"]["fap_required_documents"] == [
        "completed application form",
        "proof of income last 30 days",
    ]


def test_proof_corpus_forprofit_hospital_is_honest():
    fixture = demo_fixtures.load_fixture("case_04_forprofit_il")
    if fixture is None:
        return
    assert fixture["hospital"]["nonprofit"] is False
