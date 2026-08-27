"""`PhaxioFaxClient` / `LobMailClient` -- the clients that actually talk to a
vendor -- exercised against a faked HTTP transport.

WHAT IS FAKED AND WHAT IS NOT
-----------------------------
`requests.post` is replaced, and nothing else. Every line of these clients
runs for real: the credential gate, the destination allowlist, the multipart
body they build, the response parsing, the degrade-to-stub handler. The
recorder captures the exact kwargs the client passed and hands back a body
copied from the vendors' own published reference:

  * Phaxio: {"success":true,"message":"Fax queued for sending","data":{"id":1234}}
    -- verbatim from https://www.phaxio.com/docs/api/v2/faxes/create_and_send_fax
  * Lob: a certified-letter object with `id` ("ltr_..."), `tracking_number`
    and `tracking_events` -- fields and semantics from Lob's own OpenAPI
    spec, https://github.com/lob/lob-openapi,
    `resources/letters/models/certified.yml`, whose own note reads "Dummy
    tracking numbers are created in test mode" / tracking_events "Not
    populated in test mode".

WHAT THIS DOES NOT PROVE. No Phaxio or Lob account exists (see this package's
README). Nothing here shows that a real vendor accepts these request bodies,
only that the client sends what the published references describe. That gap
closes the first time a human runs one test key through it, and not before.
"""

from __future__ import annotations

import pytest

requests = pytest.importorskip("requests")

from delivery.vendors import (  # noqa: E402
    ProductionCredentialError,
    UnsafeDestinationError,
)
from delivery.vendors.fax import PhaxioFaxClient  # noqa: E402
from delivery.vendors.mail import LobMailClient  # noqa: E402

# NANP 555-0142: inside the reserved-fictional 555-0100..0199 block.
SAFE_FAX = "+18005550142"
SAFE_ADDRESS = {
    "name": "Sandbox Hospital",
    "line1": "1 Demo Plaza",
    "city": "Sandbox",
    "state": "CA",
    "zip": "00000",
}

# Stands in for a hospital's real contact details. Deliberately not real --
# this repo is public -- but shaped like a routable destination: 555-1234 is
# OUTSIDE the reserved 555-01XX block, and 60453 is an assigned ZIP.
REAL_HOSPITAL_FAX = "+17085551234"
REAL_HOSPITAL_ADDRESS = {
    "name": "Some Hospital",
    "line1": "4440 W 95th St",
    "city": "Oak Lawn",
    "state": "IL",
    "zip": "60453",
}

PHAXIO_CREATE_FAX_BODY = {
    "success": True,
    "message": "Fax queued for sending",
    "data": {"id": 1234},
}
LOB_CERTIFIED_LETTER_BODY = {
    "id": "ltr_4868c3b754655f90",
    "object": "letter",
    "carrier": "USPS",
    "extra_service": "certified",
    "tracking_number": "9407300000000000000000",
    "tracking_events": [],
    "expected_delivery_date": "2026-09-02",
}


class _Response:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self._body


class _RecordingTransport:
    """Stands in for `requests.post`. Records every call; a call is the thing
    under test, because "did anything leave this process?" is the question."""

    def __init__(self, body: dict, status: int = 200, raises: Exception | None = None) -> None:
        self.body = body
        self.status = status
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return _Response(self.body, self.status)


@pytest.fixture
def phaxio_http(monkeypatch):
    transport = _RecordingTransport(PHAXIO_CREATE_FAX_BODY)
    monkeypatch.setattr(requests, "post", transport)
    return transport


@pytest.fixture
def lob_http(monkeypatch):
    transport = _RecordingTransport(LOB_CERTIFIED_LETTER_BODY)
    monkeypatch.setattr(requests, "post", transport)
    return transport


@pytest.fixture(autouse=True)
def _no_ambient_vendor_env(monkeypatch):
    """These clients read the environment in their constructors. A stray
    PHAXIO_API_KEY in a developer's shell would otherwise change what these
    tests mean."""
    for var in (
        "PHAXIO_API_KEY",
        "PHAXIO_API_SECRET",
        "PHAXIO_CALLBACK_URL",
        "PHAXIO_API_MODE",
        "LOB_API_KEY",
        "DEMO_FAX_ALLOWLIST",
        "DEMO_MAIL_ALLOWLIST",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# THE ACCEPTANCE CRITERION: a production key can never reach a real destination
# ---------------------------------------------------------------------------


class TestNoProductionSendIsReachable:
    """§4 persona 4's guardrail, stated as the property the whole change
    hangs off: **there is no code path in which a production credential plus a
    real hospital destination produces a send.**

    Both tests below assert on `transport.calls == []` rather than on an
    exception type alone, because the only fact that matters is that no HTTP
    request left this process. Before this change both of them made the
    request: the destination allowlist could be widened by an env var that
    `services/intake/scripts/go_live.sh` wires from Secret Manager, and
    nothing anywhere inspected the API key at all.
    """

    def test_production_phaxio_key_plus_real_hospital_fax_sends_nothing(
        self, phaxio_http, monkeypatch
    ):
        # The worst realistic misconfiguration, both halves at once: a live
        # key in the environment AND the hospital's own number named in the
        # operator allowlist.
        monkeypatch.setenv("DEMO_FAX_ALLOWLIST", REAL_HOSPITAL_FAX)
        client = PhaxioFaxClient(api_key="livekey123456", api_secret="livesecret654321")

        with pytest.raises(Exception) as excinfo:  # noqa: B017 -- see the assertions below
            client.send("fil_danger", b"%PDF patient health information", REAL_HOSPITAL_FAX)

        assert phaxio_http.calls == [], "a fax request was built for a real hospital number"
        assert isinstance(excinfo.value, ProductionCredentialError)
        assert "test" in str(excinfo.value).lower()

    def test_production_lob_key_plus_real_hospital_address_sends_nothing(
        self, lob_http, monkeypatch
    ):
        monkeypatch.setenv("DEMO_MAIL_ALLOWLIST", "4440 W 95th St|Oak Lawn|IL|60453")
        client = LobMailClient(api_key="live_abc123")

        with pytest.raises(Exception) as excinfo:  # noqa: B017
            client.send("fil_danger", b"%PDF patient health information", REAL_HOSPITAL_ADDRESS)

        assert lob_http.calls == [], "a certified letter was addressed to a real hospital"
        assert isinstance(excinfo.value, ProductionCredentialError)
        assert "live" in str(excinfo.value).lower()

    def test_each_half_alone_is_also_refused(self, phaxio_http, lob_http, monkeypatch):
        """Neither gate is load-bearing on its own: a production key to a SAFE
        destination is refused, and a test key to a REAL destination is
        refused."""
        monkeypatch.setattr(requests, "post", phaxio_http)
        with pytest.raises(ProductionCredentialError):
            PhaxioFaxClient(api_key="livekey", api_secret="livesecret").send("f", b"%PDF", SAFE_FAX)
        assert phaxio_http.calls == []

        with pytest.raises(UnsafeDestinationError):
            PhaxioFaxClient(api_key="test_key", api_secret="test_secret").send(
                "f", b"%PDF", REAL_HOSPITAL_FAX
            )
        assert phaxio_http.calls == []

        monkeypatch.setattr(requests, "post", lob_http)
        with pytest.raises(ProductionCredentialError):
            LobMailClient(api_key="live_abc").send("f", b"%PDF", SAFE_ADDRESS)
        assert lob_http.calls == []

        with pytest.raises(UnsafeDestinationError):
            LobMailClient(api_key="test_abc").send("f", b"%PDF", REAL_HOSPITAL_ADDRESS)
        assert lob_http.calls == []

    def test_a_key_of_unknown_provenance_is_refused_not_attempted(self, phaxio_http):
        """Phaxio publishes no prefix distinguishing test from live keys, so a
        bare key is unknowable -- and unknowable means refuse. Silence is not
        consent to dial a phone."""
        client = PhaxioFaxClient(api_key="8f3ac1d9e7b24", api_secret="9d1e77fa0c4b2")
        with pytest.raises(ProductionCredentialError, match="PHAXIO_API_MODE"):
            client.send("f", b"%PDF", SAFE_FAX)
        assert phaxio_http.calls == []

    def test_operator_attestation_is_the_only_way_to_use_an_unmarked_phaxio_key(
        self, phaxio_http, monkeypatch
    ):
        monkeypatch.setenv("PHAXIO_API_MODE", "test")
        result = PhaxioFaxClient(api_key="8f3ac1d9e7b24", api_secret="9d1e77fa0c4b2").send(
            "f", b"%PDF", SAFE_FAX
        )
        assert len(phaxio_http.calls) == 1
        assert result.simulated is True

    def test_a_non_test_attestation_does_not_unlock_anything(self, phaxio_http, monkeypatch):
        monkeypatch.setenv("PHAXIO_API_MODE", "live")
        with pytest.raises(ProductionCredentialError):
            PhaxioFaxClient(api_key="8f3ac1d9e7b24", api_secret="9d1e77fa0c4b2").send(
                "f", b"%PDF", SAFE_FAX
            )
        assert phaxio_http.calls == []


# ---------------------------------------------------------------------------
# The request each client actually builds
# ---------------------------------------------------------------------------


class TestPhaxioRequestShape:
    def test_test_mode_send_posts_the_documented_request(self, phaxio_http):
        client = PhaxioFaxClient(
            api_key="test_key", api_secret="test_secret", callback_url="https://example/cb"
        )
        result = client.send("fil_7", b"%PDF-1.7 filled ppdr form", SAFE_FAX)

        assert len(phaxio_http.calls) == 1
        call = phaxio_http.calls[0]
        assert call["url"] == "https://api.phaxio.com/v2/faxes"
        assert call["auth"] == ("test_key", "test_secret")  # HTTP basic, per the reference
        assert call["data"]["to"] == SAFE_FAX  # E.164
        assert call["data"]["callback_url"] == "https://example/cb"
        filename, payload, content_type = call["files"]["file"]
        assert filename == "fil_7.pdf"
        assert payload == b"%PDF-1.7 filled ppdr form"
        assert content_type == "application/pdf"

        # data.id, per the documented response body.
        assert result.vendor == "phaxio"
        assert result.vendor_id == "1234"
        assert result.status == "sent"
        assert result.proof["phaxio_id"] == "1234"
        assert result.proof["mode"] == "test"

    def test_a_test_mode_send_is_simulated_even_though_the_vendor_is_real(self, phaxio_http):
        """The heart of the `simulated` contract. Phaxio returns a genuine fax
        id for a test-key call and places no phone call. Reporting that as a
        live send -- which deriving the flag from `vendor != "fake"` does --
        is defect #6 wearing a real vendor's name."""
        result = PhaxioFaxClient(api_key="test_key", api_secret="test_secret").send(
            "fil_8", b"%PDF", SAFE_FAX
        )
        assert result.vendor == "phaxio"
        assert result.vendor_id == "1234"
        assert result.simulated is True

    def test_a_response_without_a_fax_id_degrades_instead_of_inventing_one(self, monkeypatch):
        monkeypatch.setattr(requests, "post", _RecordingTransport({"success": True, "data": {}}))
        result = PhaxioFaxClient(api_key="test_key", api_secret="test_secret").send(
            "fil_9", b"%PDF", SAFE_FAX
        )
        assert result.vendor == "fake"
        assert result.simulated is True
        assert "ValueError" in result.proof["fallback_reason"]


class TestLobRequestShape:
    def test_test_mode_send_posts_the_documented_certified_letter(self, lob_http):
        client = LobMailClient(api_key="test_abc123")
        result = client.send("fil_10", b"%PDF-1.7 validation letter", SAFE_ADDRESS)

        assert len(lob_http.calls) == 1
        call = lob_http.calls[0]
        assert call["url"] == "https://api.lob.com/v1/letters"
        assert call["auth"] == ("test_abc123", "")  # key as username, empty password
        data = call["data"]
        assert data["to[address_line1]"] == "1 Demo Plaza"
        assert data["to[address_zip]"] == "00000"
        assert data["from[address_zip]"] == "00000"
        assert data["extra_service"] == "certified"  # singular, per the OpenAPI spec
        assert data["color"] == "false"
        # Required by letter_editable.yml; its absence 422s a live request.
        assert data["use_type"] == "operational"
        filename, payload, content_type = call["files"]["file"]
        assert filename == "fil_10.pdf"
        assert content_type == "application/pdf"

        assert result.vendor == "lob"
        assert result.vendor_id == "ltr_4868c3b754655f90"
        assert result.proof["tracking"] == "9407300000000000000000"
        assert result.proof["mode"] == "test"

    def test_a_test_mode_letter_is_simulated_despite_a_real_letter_id(self, lob_http):
        """Lob's own spec on the tracking number a test key returns: "Dummy
        tracking numbers are created in test mode." A dummy tracking number in
        the audit trail must not be presented as a mailed letter."""
        result = LobMailClient(api_key="test_abc123").send("fil_11", b"%PDF", SAFE_ADDRESS)
        assert result.vendor == "lob"
        assert result.vendor_id.startswith("ltr_")
        assert result.simulated is True

    def test_the_return_address_must_also_clear_the_allowlist(self, lob_http):
        """A certified letter that cannot be delivered comes back to the FROM
        address, so the return address is a destination too. The constructor
        has always claimed this was checked; it was not."""
        client = LobMailClient(
            api_key="test_abc123",
            from_address={
                "name": "Advocate",
                "address_line1": "4440 W 95th St",
                "address_city": "Oak Lawn",
                "address_state": "IL",
                "address_zip": "60453",
            },
        )
        with pytest.raises(UnsafeDestinationError):
            client.send("fil_12", b"%PDF", SAFE_ADDRESS)
        assert lob_http.calls == []


# ---------------------------------------------------------------------------
# No credentials, and vendor failure: both must stay honest
# ---------------------------------------------------------------------------


class TestNoCredentialsAndDegradation:
    def test_no_phaxio_credentials_is_a_labelled_simulation_not_a_send(self, phaxio_http):
        """Requirement 3: with nothing configured the behaviour is exactly as
        safe as it has always been -- a stub send, plainly labelled, no
        network call, and never an exception the pipeline has to swallow."""
        result = PhaxioFaxClient().send("fil_13", b"%PDF", SAFE_FAX)
        assert phaxio_http.calls == []
        assert result.vendor == "fake"
        assert result.simulated is True
        assert result.status == "sent"
        assert result.proof["mode"] == "stub"

    def test_no_lob_credentials_is_a_labelled_simulation_not_a_send(self, lob_http):
        result = LobMailClient().send("fil_14", b"%PDF", SAFE_ADDRESS)
        assert lob_http.calls == []
        assert result.vendor == "fake"
        assert result.simulated is True
        assert result.proof["mode"] == "stub"

    def test_missing_credentials_still_enforce_the_destination_allowlist(self, phaxio_http):
        """The stub path is not a bypass: an unconfigured system still refuses
        a real destination rather than quietly recording it."""
        with pytest.raises(UnsafeDestinationError):
            PhaxioFaxClient().send("fil_15", b"%PDF", REAL_HOSPITAL_FAX)

    def test_a_vendor_outage_degrades_but_says_why(self, monkeypatch):
        """Falling back to a stub keeps a vendor outage from blocking a filing.
        Falling back WITHOUT a trace is this project's signature defect, so the
        reason rides into `filings/{filing_id}.proof`."""
        transport = _RecordingTransport({}, raises=requests.ConnectionError("dns failure"))
        monkeypatch.setattr(requests, "post", transport)
        result = PhaxioFaxClient(api_key="test_k", api_secret="test_s").send(
            "fil_16", b"%PDF", SAFE_FAX
        )
        assert len(transport.calls) == 1  # it really did try
        assert result.vendor == "fake"
        assert result.simulated is True
        assert result.proof["attempted_vendor"] == "phaxio"
        assert "dns failure" in result.proof["fallback_reason"]

    def test_an_http_error_degrades_but_says_why(self, monkeypatch):
        monkeypatch.setattr(requests, "post", _RecordingTransport({"error": "unauthorized"}, 401))
        result = LobMailClient(api_key="test_abc").send("fil_17", b"%PDF", SAFE_ADDRESS)
        assert result.vendor == "fake"
        assert result.simulated is True
        assert result.proof["attempted_vendor"] == "lob"
        assert "401" in result.proof["fallback_reason"]

    def test_a_safety_refusal_is_never_degraded_into_a_quiet_stub(self, phaxio_http):
        """The degrade handler must not swallow the guardrails. A
        ProductionCredentialError that came back as a cheerful `simulated`
        stub would hide the one fact a human needs to act on: a production key
        is sitting in this system's environment."""
        with pytest.raises(ProductionCredentialError):
            PhaxioFaxClient(api_key="livekey", api_secret="livesecret").send(
                "fil_18", b"%PDF", SAFE_FAX
            )
        with pytest.raises(UnsafeDestinationError):
            PhaxioFaxClient(api_key="test_k", api_secret="test_s").send(
                "fil_19", b"%PDF", REAL_HOSPITAL_FAX
            )
