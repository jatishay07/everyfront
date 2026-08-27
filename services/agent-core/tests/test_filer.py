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


# --------------------------------------------------------------------------
# HANDOFF.md defect #6, SWARM WO8 half: `filings/{filing_id}.simulated`
#
# Live before this change (`GET /cases/{id}` -> filings[]):
#
#     {'front': 'audit', 'channel': 'mail', 'status': 'sent',
#      'vendor_id': 'fake-ltr_1f0ae92e7adb44e3946e', 'simulated': None}
#
# Every one of those was a FakeMailVendor recording, and the record said
# "sent" with nothing beside it. The flag existed only inside `proof` -- and
# only when RELAY happened to put it there -- while the top-level record the
# dashboard, `GET /cases/{id}` and a judge all read had no such field at all.
# --------------------------------------------------------------------------


def test_filing_record_carries_a_top_level_simulated_flag(monkeypatch):
    """The real end-to-end path with no Phaxio/Lob credentials -- i.e. every
    filing this system has ever made. It must record itself as simulated."""
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    s.create_case("c5", {"hospital": {"name": "Test Hospital"}, "bill": {}})

    asyncio.run(filer.run("c5", s.get_case("c5"), "audit", filing_id="f-audit"))

    filings = s.list_filings("c5")
    assert len(filings) == 1
    record = filings[0]
    assert "simulated" in record, "filings/{id} has no simulated field at all"
    assert record["simulated"] is True
    # And the vendor that actually ran rides along, so the two facts can be
    # checked against each other by anyone reading the record.
    assert record["vendor"] == "fake"


def test_a_vendor_result_missing_the_flag_is_not_recorded_as_a_live_send(monkeypatch):
    """Defect #6 verbatim: `send_filing` shipped for weeks without ever
    setting `"simulated"`, and `bool(vendor_result.get("simulated"))` turned
    that silence into a confident `False`. If the delivery layer ever stops
    reporting the fact again, the record must not claim the send was real."""
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    s.create_case("c6", {"hospital": {"name": "Test Hospital"}, "bill": {}})
    monkeypatch.setattr(
        filer.delivery_bridge,
        "deliver",
        lambda **kw: {"vendor_id": "fake-ltr_x", "status": "sent", "vendor": "fake"},
    )

    result = asyncio.run(filer.run("c6", s.get_case("c6"), "audit", filing_id="f-nokey"))

    assert s.list_filings("c6")[0]["simulated"] is True
    assert result["fact"]["simulated"] is True


def test_a_genuinely_live_send_is_recorded_as_not_simulated(monkeypatch):
    """The other direction, and the one that matters the day someone mints a
    Lob key: a real send must report `simulated: false` truthfully. Reporting
    everything as simulated forever would be its own fabrication."""
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    s.create_case("c7", {"hospital": {"name": "Test Hospital"}, "bill": {}})
    monkeypatch.setattr(
        filer.delivery_bridge,
        "deliver",
        lambda **kw: {
            "vendor_id": "ltr_realid",
            "status": "sent",
            "vendor": "lob",
            "simulated": False,
        },
    )

    result = asyncio.run(filer.run("c7", s.get_case("c7"), "audit", filing_id="f-live"))

    assert s.list_filings("c7")[0]["simulated"] is False
    assert result["fact"]["simulated"] is False


def test_filer_hands_back_the_rendered_pdf_for_the_drive_mirror(monkeypatch):
    """`pipeline.run_filer` mirrors this exact artifact to the case's Drive
    folder. It rides beside `fact`, never inside it -- `fact` is fed to the
    model as a tool result and hashed into the event id."""
    s = make_memory_store()
    monkeypatch.setattr(filer, "store", s)
    s.create_case("c8", {"hospital": {"name": "Test Hospital"}, "bill": {}})

    result = asyncio.run(filer.run("c8", s.get_case("c8"), "audit", filing_id="f-pdf"))

    assert result["pdf"][:4] == b"%PDF"
    assert len(result["pdf"]) == result["fact"]["pdf_bytes"]
    assert "pdf" not in result["fact"]
