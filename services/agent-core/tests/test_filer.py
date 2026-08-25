"""agent_core.agents.filer -- persona 5 WO6 tasks 1 + 2:

  1. Filer must actually reach RELAY's real vendor interface and get back a
     vendor id, not raise. The destination shapes filer.py used to build
     (the real C2C fax number, and a mail dict with no line1/city/state/zip)
     both fail RELAY's own in-code allowlist (packages/delivery/vendors/
     allowlist.py) unconditionally -- even the FakeVendor fallback calls it.
  2. Every rendered filing must be saved as a case document (§3.1
     `generated_application`/`generated_letter`) so it is retrievable, not
     just sent and forgotten.

`common.run_agent_turn`'s own fallback logic (see agents/common.py) degrades
to an empty narration + recorded error on any model/credential failure
without raising, so these tests exercise the REAL `filer.run()` end to end
(real RELAY rendering, real RELAY vendor allowlist + FakeVendor fallback,
real store.add_document) without needing live model credentials -- only the
LLM's one-sentence narration is allowed to come back empty in this
environment.
"""

from __future__ import annotations

import asyncio

from _helpers import make_memory_store
from agent_core.agents import filer


def test_filer_run_ppdr_reaches_a_real_vendor_and_does_not_raise(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    case_id = "c1"
    s.create_case(case_id, {"hospital": {"name": "Test Hospital"}, "bill": {"amount_cents": 5000}})
    case = s.get_case(case_id)

    result = asyncio.run(filer.run(case_id, case, "ppdr", filing_id="f-ppdr"))
    fact = result["fact"]

    assert fact["channel"] == "fax"
    assert fact["form_id"] == "cms_ppdr"
    assert fact["vendor_id"]  # RELAY returned something, real or FakeFaxVendor
    assert fact["pdf_bytes"] > 1000  # a real filled PDF, not a placeholder
    assert fact["real_destination"] == filer.PPDR_FAX_NUMBER


def test_filer_run_charity_care_reaches_a_real_vendor_and_does_not_raise(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    case_id = "c2"
    s.create_case(
        case_id,
        {
            "hospital": {"name": "Sutter Bay Hospitals"},
            "bill": {"hospital_ein": "94-0562680", "amount_cents": 262500},
        },
    )
    case = s.get_case(case_id)

    result = asyncio.run(filer.run(case_id, case, "charity_care", filing_id="f-charity"))
    fact = result["fact"]

    assert fact["channel"] == "mail"
    assert fact["form_id"] == "sutter_fap"
    assert fact["vendor_id"]
    assert fact["pdf_bytes"] > 1000


def test_filer_run_saves_the_generated_pdf_as_a_case_document(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    case_id = "c3"
    s.create_case(case_id, {"hospital": {"name": "Test Hospital"}, "bill": {}})
    case = s.get_case(case_id)

    result = asyncio.run(filer.run(case_id, case, "debt_validation", filing_id="f-debt"))
    fact = result["fact"]

    assert fact["doc_id"]
    docs = s.list_documents(case_id)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["doc_id"] == fact["doc_id"]
    assert doc["type"] == "generated_letter"
    assert doc["extracted"]["front"] == "debt_validation"
    assert doc["extracted"]["form_id"] == "debt_validation_letter"
    # No GCS_DOCUMENTS_BUCKET configured in this test environment -- degrades
    # to None rather than raising; the document record still gets created.
    assert doc["gcs_uri"] is fact["gcs_uri"]


def test_filer_run_charity_care_is_a_generated_application_not_a_letter(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    case_id = "c4"
    s.create_case(case_id, {"hospital": {"name": "Sutter"}, "bill": {"hospital_ein": "94-0562680"}})
    case = s.get_case(case_id)

    asyncio.run(filer.run(case_id, case, "charity_care", filing_id="f-app"))
    doc = s.list_documents(case_id)[0]
    assert doc["type"] == "generated_application"
