"""agent_core.delivery_bridge -- the seam between Filer and RELAY's real
packages/delivery (real filled PDFs + vendor sends), rewired 2026-08-25 after
this bridge's own try/except had been silently swallowing an ImportError and
filing five-line placeholder text instead of the real forms (see the
module's own docstring). These tests exercise the bridge's real functions
against RELAY's real package -- no mocking the thing we need to prove is
actually wired -- and only fake the network edge (vendor credentials), which
RELAY's own FakeFaxVendor/FakeMailVendor already do when no live vendor
config is present.
"""

from __future__ import annotations

from agent_core import delivery_bridge


def test_form_for_front_ppdr():
    assert delivery_bridge.form_for_front("ppdr", {}) == "cms_ppdr"


def test_form_for_front_debt_validation():
    assert delivery_bridge.form_for_front("debt_validation", {}) == "debt_validation_letter"


def test_form_for_front_audit():
    assert delivery_bridge.form_for_front("audit", {}) == "records_request_letter"


def test_form_for_front_charity_care_resolves_by_hospital_ein():
    case = {"bill": {"hospital_ein": "36-2169147"}}
    assert delivery_bridge.form_for_front("charity_care", case) == "advocate_fap"


def test_form_for_front_charity_care_falls_back_to_default_form():
    case = {"bill": {"hospital_ein": "00-0000000"}}
    assert delivery_bridge.form_for_front("charity_care", case) == "sutter_fap"


def test_channel_for_front_matches_playbook_1_2():
    assert delivery_bridge.channel_for_front("ppdr") == "fax"
    assert delivery_bridge.channel_for_front("charity_care") == "mail"
    assert delivery_bridge.channel_for_front("debt_validation") == "mail"
    assert delivery_bridge.channel_for_front("audit") == "mail"


def test_render_filing_pdf_produces_a_real_pdf_not_a_placeholder():
    """The whole point of the rewire: this must be RELAY's actual filled
    form (a real PDF, hundreds of bytes+), never SWARM's old 5-line stub."""
    case = {
        "patient": {"name": "SYNTHETIC -- TEST", "household_size": 2},
        "bill": {"hospital_ein": "36-2169147", "amount_cents": 123456},
    }
    pdf_bytes, form_id = delivery_bridge.render_filing_pdf(
        "charity_care", case, {"filing_id": "f1"}
    )
    assert form_id == "advocate_fap"
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000  # a real filled form, not a placeholder


def test_render_filing_pdf_ppdr_is_the_real_cms_form():
    case = {"bill": {"amount_cents": 50000}}
    pdf_bytes, form_id = delivery_bridge.render_filing_pdf("ppdr", case, {"filing_id": "f2"})
    assert form_id == "cms_ppdr"
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_deliver_returns_a_vendor_id_for_an_allowlisted_test_destination():
    """`deliver` is a thin pass-through to RELAY's vendor interface, which
    enforces its own destination allowlist IN CODE (packages/delivery/vendors/
    allowlist.py) -- it will refuse the REAL C2C fax number just as readily as
    a real hospital's. Callers (agents/filer.py) are responsible for routing
    to one of RELAY's documented safe test destinations; this test uses one."""
    result = delivery_bridge.deliver(
        filing_id="f3",
        case_id="c1",
        front="ppdr",
        pdf=b"%PDF-1.4 fake",
        destination="+18005550142",  # NANP 555-0142: reserved-fictional range
        channel="fax",
    )
    assert "vendor_id" in result
    assert result.get("status") in ("sent", "queued", None) or result.get("status")


def test_deliver_refuses_a_real_looking_destination():
    """The guardrail itself: RELAY's allowlist must still reject a
    real-looking number even when reached through this bridge."""
    from delivery.vendors.allowlist import UnsafeDestinationError

    try:
        delivery_bridge.deliver(
            filing_id="f4",
            case_id="c1",
            front="ppdr",
            pdf=b"%PDF-1.4 fake",
            destination="888-610-4092",  # the real C2C fax line, §1.2
            channel="fax",
        )
        raised = False
    except UnsafeDestinationError:
        raised = True
    assert raised


# ------------------------------------------------------------------ simulated
#
# The seam that produced HANDOFF.md defect #6. RELAY's `send_filing` computes
# the fact; this bridge is the only thing that reads it; a falsy default in
# between turned "not reported" into "definitely live" for every filing this
# system has ever made.


def test_send_filing_really_does_report_the_simulated_flag():
    """The contract this bridge depends on, asserted against RELAY's ACTUAL
    module rather than a stand-in -- the mismatch that made `delivery.fax` vs
    `delivery.vendors.fax` invisible for weeks was exactly a seam nobody
    tested end to end. With no Phaxio credentials this falls through to
    FakeFaxVendor, which is the state of the world today."""
    result = delivery_bridge.deliver(
        filing_id="f-sim",
        case_id="c1",
        front="ppdr",
        pdf=b"%PDF-1.4 fake",
        destination="+18005550142",
        channel="fax",
    )
    assert result["vendor"] == "fake"
    assert result["simulated"] is True
    assert delivery_bridge.simulated_flag(result) is True


def test_simulated_flag_does_not_read_an_absent_key_as_a_live_send():
    """`bool(vendor_result.get("simulated"))` is the whole of defect #6."""
    assert delivery_bridge.simulated_flag({"vendor_id": "fake-ltr_x", "status": "sent"}) is True
    assert delivery_bridge.simulated_flag({"simulated": None}) is True


def test_simulated_flag_reports_an_explicitly_live_send_as_live():
    assert delivery_bridge.simulated_flag({"vendor": "lob", "simulated": False}) is False


def test_simulated_flag_never_sniffs_the_vendor_id_prefix():
    """A vendor-id format is a string RELAY is free to change, not a
    statement about whether anything left the building. The flag is the flag."""
    assert delivery_bridge.simulated_flag({"vendor_id": "fake-ltr_x", "simulated": False}) is False
    assert delivery_bridge.simulated_flag({"vendor_id": "ltr_real", "simulated": True}) is True


def test_google_sync_is_reported_unconfigured_without_a_refresh_token(monkeypatch):
    """The state of this repo today, and the one the no-op paths depend on."""
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    assert delivery_bridge.google_sync_configured() is False
