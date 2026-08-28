"""END-TO-END GMAIL INTAKE, FAKED AT THE HTTP TRANSPORT -- NOT AT OUR OWN FUNCTIONS.

WHY THIS MODULE EXISTS
======================
Every other test in this directory replaces `gmail_client._service()` with a
hand-rolled object that answers `.users().messages().get(...).execute()`. That
proves our code calls our code. It cannot catch the class of defect that has
produced twelve of this project's bugs: a call that is shaped right, passes
every test, and is rejected or misread by the real library at runtime. Two
concrete examples this repo has already lived through --
`history.list(labelIds=[...])` (the real parameter is `labelId`, singular, and
`googleapiclient.discovery` validates kwargs against the discovery document's
argmap and raises `TypeError` at the first live push), and defect #7, a payload
whose field names the consumer never read.

So this module fakes ONE layer lower. It builds a REAL
`googleapiclient.discovery` service from the REAL Gmail v1 discovery document
that ships inside the pinned `google-api-python-client==2.199.0` wheel, and
substitutes only the bottom-most HTTP transport. Everything between our code
and that seam is Google's own code, running for real:

  * `intake.google_auth.load_user_credentials` -> a real
    `google.oauth2.credentials.Credentials` object,
  * `googleapiclient.discovery.build("gmail", "v1", credentials=...)` -> real
    static discovery, real `Resource` construction, real method argmaps,
  * kwarg validation (`labelId` vs `labelIds` is a `TypeError` here, exactly as
    it would be against Google),
  * real URL/query-string construction from the discovery `path` templates,
  * real response deserialisation (`JsonModel`), and real
    `googleapiclient.errors.HttpError` on a non-2xx -- so `_is_history_expired`
    is exercised against the genuine exception type rather than a lookalike.

WHERE EACH FIXTURE SHAPE CAME FROM
==================================
Nothing below is invented. Every field name and type is either read out of the
discovery document at
`<site-packages>/googleapiclient/discovery_cache/documents/gmail.v1.json`
(google-api-python-client 2.199.0, doc `revision` 20260727) or taken from
Google's published Gmail API reference. `test_discovery_document_still_pins_
these_shapes` re-reads that file at test time and fails if any of it drifts, so
these citations cannot rot silently.

  * `Message` / `MessagePart` / `MessagePartBody` / `MessagePartHeader`
    schemas -- gmail.v1.json `schemas`. In particular `MessagePartBody`:
    "When present, contains the ID of an external attachment that can be
    retrieved in a separate `messages.attachments.get` request. When not
    present, the entire content of the message part body is contained in the
    data field."  (This is why an attachment is two round-trips.)
  * `ListHistoryResponse` / `History` / `HistoryMessageAdded` -- same file.
    `History.messages` is documented as typically carrying only `id` and
    `threadId`, which is why the fixtures below do exactly that.
  * `users.history.list` parameters -- `startHistoryId` (query, string),
    `historyTypes` (query, repeated), `labelId` (query, string, singular,
    "Only return messages with a label matching the ID."), `pageToken`.
  * The 404: `startHistoryId`'s own description in that file -- "Supplying an
    invalid or out of date `startHistoryId` typically returns an `HTTP 404`
    error code ... If you receive an `HTTP 404` error response, your
    application should perform a full sync."
  * The error BODY shape (`{"error": {"code", "message", "errors": [...],
    "status"}}`) -- Google API error response format,
    https://developers.google.com/gmail/api/guides/handle-errors .
  * `WatchResponse` (`historyId`, `expiration`) and `WatchRequest`
    (`topicName`, `labelIds`) -- gmail.v1.json `schemas`, and
    https://developers.google.com/gmail/api/guides/push .
  * The Pub/Sub push notification body Gmail publishes --
    `{"emailAddress": ..., "historyId": <number>}`, base64-encoded into a
    Pub/Sub push envelope -- from the same push guide. `historyId` arrives as a
    JSON NUMBER there, not a string; the fixtures keep it a number on purpose.
  * The MIME trees are not hand-written at all: they are produced by Python's
    stdlib `email` package from the repo's own synthetic fixture bill, then
    translated into Gmail's parsed `payload` shape by `_gmail_payload`, whose
    docstring states the translation rules and their source.

WHAT THIS CANNOT PROVE -- READ THIS BEFORE QUOTING THIS SUITE
=============================================================
A fake cannot prove that Google's real responses match these fixtures. It
proves that IF Gmail answers in the shape its own discovery document and
documentation describe, this service handles it correctly end to end. It does
NOT prove, and nobody may report it as proving:

  * that the OAuth refresh token flow works -- no token has ever been minted,
    and the transport seam sits BELOW the point where credentials are
    exchanged, so `Credentials.refresh()` never runs here;
  * that Gmail accepts our `users.watch` request, or that
    `gmail-api-push@system.gserviceaccount.com` actually holds
    `pubsub.publisher` on the topic (a live-only failure, and the single most
    likely one -- see `scripts/verify_live.sh` step 3);
  * that a real Gmail message's parsed `payload` tree is shaped like these
    fixtures for every mail client that might send the demo bill;
  * that the real `history.list` window, ordering, or pagination behaves as
    modelled here;
  * anything at all about GCS or Pub/Sub as services. Those two are faked at
    the client-object level (`google.cloud.storage.Client`), one layer higher
    than Gmail is, because they are not the unexercised risk this module
    exists to shrink.

The only thing that closes those gaps is a minted token and
`services/intake/scripts/verify_live.sh`.

WHY THIS MODULE IMPORTS `googleapiclient` AND `test_gmail_history.py` REFUSES TO
===============================================================================
That module's docstring says a module-level `import googleapiclient` "would
pass CI and fail on the maintainer's machine at collection time", and that was
the right call for a suite that did not need it. This one cannot exist without
it. `google-api-python-client==2.199.0` is a pinned dependency of THIS service
(`services/intake/requirements.txt`) and CI installs every
`services/*/requirements.txt`, so the precondition for running this directory's
tests is simply `pip install -r services/intake/requirements.txt`. It is
imported at module level, not `importorskip`'d, deliberately: a suite whose
whole purpose is to be the last line of defence before a live token must not be
able to skip itself into a green run.

HANDOFF -> FORGE/ATLAS: `.github/workflows/ci.yml` runs
`pytest services/agent-core/tests` and `pytest services/api/tests` but has no
step for `pytest services/intake/tests`. Root `testpaths` is
`["tests", "packages"]`, so this service's whole suite -- 53 pre-existing tests
plus everything here -- runs on nobody's machine but ours. Please add:
    - name: Service tests (intake)
      run: pytest services/intake/tests -m "not e2e"
`.github/` is outside RELAY's owned paths (BUILD_PLAYBOOK.md §0.2).
"""

from __future__ import annotations

import ast
import base64
import json
import re
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httplib2
import pytest
from google.api_core.exceptions import PreconditionFailed
from googleapiclient import _auth as googleapiclient_auth
from googleapiclient import discovery as googleapiclient_discovery
from googleapiclient.errors import HttpError
from intake import gmail_client, pipeline, pubsub, state

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_BILL = REPO_ROOT / "fixtures/generated/cases/case_01_uninsured_gfe_ca/documents/bill.pdf"

# The demo account. Synthetic, never a real address (playbook §0.6).
DEMO_ADDRESS = "everyfront.demo@example.com"
PATIENT_ADDRESS = "jordan.alvarez@example.invalid"

BUCKET = "ef-documents-everyfront-hack-2026"
PROJECT = "everyfront-hack-2026"


# --------------------------------------------------------------------------
# Gmail response fixtures, built from real bytes and real MIME
# --------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    """Gmail returns base64url WITHOUT padding (`MessagePartBody.data`,
    format `byte`). Stripping the `=` here is what makes
    `fetch_attachment_bytes`'s re-padding load-bearing rather than decorative.
    """
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _gmail_payload(part, attachments: dict[str, bytes], part_id: str = "") -> dict:
    """Translate a stdlib `email.message.EmailMessage` into Gmail's parsed
    `MessagePart` shape.

    The translation rules come from gmail.v1.json's `MessagePart` /
    `MessagePartBody` schemas:

      * `partId` -- "The immutable ID of the message part." Gmail numbers the
        root part `""` and its children `"0"`, `"1"`, `"0.0"`, ... which is
        what this reproduces.
      * `filename` -- "Only present if this message part represents an
        attachment"; Gmail sends `""` for every other part.
      * `parts` -- "The child MIME message parts of this part. This only
        applies to container MIME message parts, for example `multipart/*`."
      * `body` -- for a container, an empty body (`{"size": 0}`); for a leaf
        with a filename, an `attachmentId` and no `data`; for a leaf without
        one, inline `data`.
      * `headers` -- the part's own RFC 2822 headers, verbatim.
    """
    out: dict = {
        "partId": part_id,
        "mimeType": part.get_content_type(),
        "filename": part.get_filename() or "",
        "headers": [{"name": k, "value": str(v)} for k, v in part.items()],
    }
    if part.is_multipart():
        out["body"] = {"size": 0}
        out["parts"] = [
            _gmail_payload(sub, attachments, f"{part_id}.{i}" if part_id else str(i))
            for i, sub in enumerate(part.iter_parts())
        ]
        return out
    data = part.get_payload(decode=True) or b""
    if out["filename"]:
        # Real Gmail attachment ids are long opaque base64url blobs. The exact
        # value is meaningless; that it is opaque and must be echoed back
        # verbatim to `messages.attachments.get` is the part that matters.
        attachment_id = f"ANGjdJ8fakeAttachmentId{len(attachments)}"
        attachments[attachment_id] = data
        out["body"] = {"attachmentId": attachment_id, "size": len(data)}
    else:
        out["body"] = {"size": len(data), "data": _b64url(data)}
    return out


def build_gmail_message(
    *,
    message_id: str,
    thread_id: str,
    history_id: str,
    attachments_out: dict[str, bytes],
    pdf_names: tuple[str, ...] = ("bill.pdf",),
    with_alternative_body: bool = False,
    extra_image: bool = False,
) -> dict:
    """A `users.messages.get?format=full` response, built from a real email.

    The MIME tree is assembled by Python's `email` package -- not typed out by
    hand -- so its structure is whatever a standards-compliant mailer really
    produces, including the `multipart/alternative` nesting Gmail's own web
    composer emits whenever a message has both a plain-text and an HTML body.
    """
    pdf_bytes = FIXTURE_BILL.read_bytes()
    msg = EmailMessage()
    msg["From"] = PATIENT_ADDRESS
    msg["To"] = DEMO_ADDRESS
    msg["Subject"] = "Hospital bill -- please help"
    msg["Date"] = "Wed, 26 Aug 2026 09:14:02 -0700"
    msg.set_content("Attaching the bill I got from the hospital. Thank you.")
    if with_alternative_body:
        # Promotes the body to multipart/alternative, and the whole message to
        # multipart/mixed once an attachment is added below -- i.e. the PDF
        # ends up as a sibling of a nested container, two levels deep.
        msg.add_alternative("<p>Attaching the bill I got from the hospital.</p>", subtype="html")
    if extra_image:
        msg.add_attachment(
            b"\x89PNG\r\n\x1a\n not really a png",
            maintype="image",
            subtype="png",
            filename="signature.png",
        )
    for name in pdf_names:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=name)

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": ["UNREAD", "INBOX"],
        "snippet": "Attaching the bill I got from the hospital. Thank you.",
        "historyId": history_id,
        "internalDate": "1756224842000",
        "payload": _gmail_payload(msg, attachments_out),
        "sizeEstimate": 71234,
    }


def history_page(
    *, record_id: str, message_id: str, thread_id: str, current_history_id: str
) -> dict:
    """One `ListHistoryResponse` page carrying a single `messagesAdded`.

    `History.messages` is documented as typically carrying only `id` and
    `threadId`; `messagesAdded[].message` is a `Message` too, and in a real
    response it carries `labelIds` as well. Neither is enough to skip the
    `messages.get`, which is the point.
    """
    stub = {"id": message_id, "threadId": thread_id}
    return {
        "history": [
            {
                "id": record_id,
                "messages": [dict(stub)],
                "messagesAdded": [{"message": dict(stub, labelIds=["UNREAD", "INBOX"])}],
            }
        ],
        "historyId": current_history_id,
    }


def gmail_push_envelope(*, pubsub_message_id: str, history_id: int) -> dict:
    """The Pub/Sub push body Cloud Pub/Sub POSTs to `/pubsub/gmail`.

    Gmail publishes `{"emailAddress": ..., "historyId": <number>}` (a JSON
    number, per the push guide) and Pub/Sub wraps it base64-encoded.
    """
    data = json.dumps({"emailAddress": DEMO_ADDRESS, "historyId": history_id})
    return {
        "message": {
            "data": base64.b64encode(data.encode()).decode(),
            "messageId": pubsub_message_id,
            "publishTime": "2026-08-26T16:14:03.123Z",
        },
        "subscription": f"projects/{PROJECT}/subscriptions/ef-intake-email",
    }


# --------------------------------------------------------------------------
# The transport fake
# --------------------------------------------------------------------------
_GOOGLE_API_ERROR_REASONS = {
    404: ("notFound", "NOT_FOUND", "Requested entity was not found."),
    403: ("forbidden", "PERMISSION_DENIED", "Insufficient Permission"),
    500: ("backendError", "INTERNAL", "Backend Error"),
}


def _error_body(code: int, message: str | None = None) -> bytes:
    """Google's API error envelope, as documented at
    https://developers.google.com/gmail/api/guides/handle-errors ."""
    reason, status, default_message = _GOOGLE_API_ERROR_REASONS[code]
    message = message or default_message
    return json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "errors": [{"message": message, "domain": "global", "reason": reason}],
                "status": status,
            }
        }
    ).encode()


class RecordedRequest:
    """One HTTP request the Gmail client actually put on the wire."""

    def __init__(self, method: str, uri: str, body):
        self.method = method
        self.uri = uri
        parsed = urlparse(uri)
        self.path = parsed.path
        self.query = parse_qs(parsed.query)
        self.body = json.loads(body) if body else None

    def __repr__(self) -> str:  # pragma: no cover -- assertion output only
        return f"<{self.method} {self.uri}>"


class GmailTransport(httplib2.Http):
    """An `httplib2.Http` that answers Gmail v1 URLs from fixtures.

    Subclasses the real transport rather than duck-typing it so every attribute
    `googleapiclient.http` reaches for (`follow_redirects`, `redirect_codes`,
    timeouts, ...) exists with its real default. Only `request()` is replaced.

    Routing is by URL, not by call order, so a wrong path, a wrong HTTP method,
    a wrong path parameter or a missing query parameter surfaces as an
    unrouted request -- not as a fixture handed to the wrong call.
    """

    def __init__(
        self,
        *,
        messages: dict[str, dict],
        attachments: dict[str, bytes],
        history: dict[str, list[dict]],
        expired_history_ids: frozenset[str] = frozenset(),
        watch_responses: list[dict] | None = None,
    ):
        super().__init__()
        self.messages = messages
        self.attachments = attachments
        self.history = history
        self.expired_history_ids = expired_history_ids
        # `is None`, not truthiness: an explicitly EMPTY list means "Gmail
        # refuses the watch", which is a case this suite tests.
        if watch_responses is None:
            watch_responses = [{"historyId": "1000500", "expiration": "1756829400000"}]
        self.watch_responses = list(watch_responses)
        self.requests: list[RecordedRequest] = []
        self.unrouted: list[RecordedRequest] = []

        self._routes = [
            (re.compile(r"^/gmail/v1/users/([^/]+)/watch$"), "POST", self._watch),
            (
                re.compile(r"^/gmail/v1/users/([^/]+)/history$"),
                "GET",
                self._history_list,
            ),
            (
                re.compile(r"^/gmail/v1/users/([^/]+)/messages/([^/]+)/attachments/([^/]+)$"),
                "GET",
                self._attachment_get,
            ),
            (
                re.compile(r"^/gmail/v1/users/([^/]+)/messages/([^/]+)$"),
                "GET",
                self._message_get,
            ),
        ]

    # -- httplib2 interface ------------------------------------------------
    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        recorded = RecordedRequest(method, uri, body)
        self.requests.append(recorded)
        for pattern, verb, handler in self._routes:
            match = pattern.match(recorded.path)
            if match and method == verb:
                status, content = handler(recorded, *match.groups())
                return self._respond(status, content)
        self.unrouted.append(recorded)
        return self._respond(
            404,
            _error_body(
                404,
                f"the Gmail transport fake has no route for {method} {recorded.path}",
            ),
        )

    @staticmethod
    def _respond(status: int, content: bytes):
        return (
            httplib2.Response(
                {"status": status, "content-type": "application/json; charset=UTF-8"}
            ),
            content,
        )

    # -- routes ------------------------------------------------------------
    def _watch(self, req: RecordedRequest, user_id: str):
        if not self.watch_responses:
            return 403, _error_body(403, "Error sending test message to Cloud PubSub projects/...")
        return 200, json.dumps(self.watch_responses.pop(0)).encode()

    def _history_list(self, req: RecordedRequest, user_id: str):
        start = req.query.get("startHistoryId", [None])[0]
        if start in self.expired_history_ids:
            return 404, _error_body(404, "Requested entity was not found.")
        pages = self.history.get(start)
        if pages is None:
            return 404, _error_body(
                404, f"the fake has no history fixture for startHistoryId={start!r}"
            )
        token = req.query.get("pageToken", [None])[0]
        for page in pages:
            if page.get("_token") == token:
                return 200, json.dumps({k: v for k, v in page.items() if k != "_token"}).encode()
        return 404, _error_body(404, f"the fake has no history page for pageToken={token!r}")

    def _message_get(self, req: RecordedRequest, user_id: str, message_id: str):
        message = self.messages.get(message_id)
        if message is None:
            return 404, _error_body(404, "Requested entity was not found.")
        return 200, json.dumps(message).encode()

    def _attachment_get(
        self, req: RecordedRequest, user_id: str, message_id: str, attachment_id: str
    ):
        raw = self.attachments.get(attachment_id)
        if raw is None:
            return 404, _error_body(404, "Requested entity was not found.")
        # gmail.v1.json `MessagePartBody`: `data` (base64url) + `size`.
        return 200, json.dumps({"size": len(raw), "data": _b64url(raw)}).encode()

    # -- assertion helpers -------------------------------------------------
    def requests_to(self, path_regex: str) -> list[RecordedRequest]:
        pattern = re.compile(path_regex)
        return [r for r in self.requests if pattern.match(r.path)]


# --------------------------------------------------------------------------
# GCS fake -- one client, shared by storage.py, state.py and dedupe.py
# --------------------------------------------------------------------------
class _FakeBlob:
    def __init__(self, store: dict, path: str):
        self._store = store
        self.path = path

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.path in self._store:
            # `google.cloud.storage` raises this on a failed generation
            # precondition -- the exact exception `dedupe.claim` catches.
            raise PreconditionFailed(
                f"At least one of the pre-conditions you specified did not hold: {self.path}"
            )
        raw = data if isinstance(data, bytes) else str(data).encode()
        self._store[self.path] = (raw, content_type)

    def exists(self) -> bool:
        return self.path in self._store

    def download_as_text(self) -> str:
        return self._store[self.path][0].decode()

    def delete(self) -> None:
        self._store.pop(self.path, None)


class _FakeBucket:
    def __init__(self, store: dict, name: str):
        self._store = store
        self.name = name

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._store, path)


class FakeStorageClient:
    """In-memory `google.cloud.storage.Client`.

    Deliberately NOT faked at the HTTP transport the way Gmail is: GCS is not
    the unexercised risk this module exists to shrink, and `intake` touches it
    through three modules (`storage`, `state`, `dedupe`) that all construct
    their own client. One shared in-memory store keeps the atomic-create
    semantics `dedupe.claim` depends on (`if_generation_match=0`) honest, which
    is the only GCS behaviour this pipeline's correctness actually rests on.
    """

    store: dict[str, tuple[bytes, str | None]] = {}

    def __init__(self, *args, **kwargs):
        pass

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(FakeStorageClient.store, name)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def gcs(monkeypatch):
    FakeStorageClient.store = {}
    monkeypatch.setattr("google.cloud.storage.Client", FakeStorageClient)
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", BUCKET)
    return FakeStorageClient.store


@pytest.fixture
def published(monkeypatch):
    """Captures `case.document.added` at the publish boundary.

    `google-cloud-pubsub` is a real dependency of this service but is not
    installed in the repo's local `.venv`, so the publisher client itself
    cannot be exercised here. Every captured payload is round-tripped through
    `json.dumps` first, because that is what `pubsub.publish` really does to it
    and an unserialisable value would otherwise only show up in production.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_publish(topic: str, data: dict) -> str:
        json.dumps(data)
        captured.append((topic, data))
        return f"pubsub-msg-{len(captured)}"

    monkeypatch.setattr(pubsub, "publish", _fake_publish)
    return captured


@pytest.fixture
def credentials(monkeypatch):
    """Real `google.oauth2.credentials.Credentials` are constructed from these.

    They are never exchanged for an access token: the transport seam sits below
    the point where that would happen. See this module's "what this cannot
    prove".
    """
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "1//fake-refresh-token")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)


def install_transport(monkeypatch, transport: GmailTransport) -> GmailTransport:
    """Substitute the transport `googleapiclient` would build from credentials.

    `discovery.build_from_document` calls `_auth.authorized_http(credentials)`
    when credentials are supplied (read out of the 2.199.0 wheel's
    `discovery.py`) -- that call is the lowest seam in the library that is
    still above the socket, so patching it leaves `_service()`, `build()`,
    discovery parsing, argmap validation, URL construction and response
    deserialisation all running for real.
    """
    monkeypatch.setattr(googleapiclient_auth, "authorized_http", lambda credentials: transport)
    return transport


@pytest.fixture
def one_bill_arrives(monkeypatch, credentials):
    """The ordinary case: a push notification, one INBOX message, one PDF."""
    attachments: dict[str, bytes] = {}
    message = build_gmail_message(
        message_id="18f0a1b2c3d4e5f6",
        thread_id="18f0a1b2c3d4e5f6",
        history_id="1000042",
        attachments_out=attachments,
    )
    transport = GmailTransport(
        messages={message["id"]: message},
        attachments=attachments,
        history={
            "1000000": [
                dict(
                    history_page(
                        record_id="1000041",
                        message_id=message["id"],
                        thread_id=message["threadId"],
                        current_history_id="1000042",
                    ),
                    _token=None,
                )
            ]
        },
    )
    return install_transport(monkeypatch, transport)


# --------------------------------------------------------------------------
# The end-to-end walk
# --------------------------------------------------------------------------
def attachment_events(published) -> list[dict]:
    """Just the ATTACHMENT events. Every fixture message here carries a real
    text/plain body (`build_gmail_message` uses `EmailMessage.set_content`),
    which now also publishes as a `patient_statement` document -- so a test
    about how attachments are enumerated, deduplicated or named has to say
    which events it means rather than counting everything on the topic.
    """
    return [e for _, e in published if e.get("doc_type") != pipeline.PATIENT_STATEMENT_TYPE]


def body_events(published) -> list[dict]:
    return [e for _, e in published if e.get("doc_type") == pipeline.PATIENT_STATEMENT_TYPE]


def test_push_notification_produces_a_case_document_added_event(
    one_bill_arrives, gcs, published, monkeypatch
):
    """Push -> history.list -> messages.get -> attachments.get -> GCS ->
    `case.document.added`, with every Gmail hop going through the real client.
    """
    transport = one_bill_arrives
    state.set_last_history_id("1000000")

    body = gmail_push_envelope(pubsub_message_id="pubsub-1", history_id=1000042)
    message_id, data = pubsub.decode_push_envelope(body)
    result = pipeline.process_gmail_push(message_id, data)

    # TWO documents, not one: the PDF, and the body of the email it was
    # attached to. `build_gmail_message` gives every fixture message a real
    # text/plain part ("Attaching the bill I got from the hospital."), which
    # is what a patient actually sends -- and what a patient writes there is
    # the only place a fact like household size can ever come from (see
    # `pipeline.PATIENT_STATEMENT_TYPE`).
    assert result["documents_published"] == 2
    assert not transport.unrouted, f"unrouted Gmail calls: {transport.unrouted}"

    # -- the calls that were actually made, in order, as URLs ---------------
    assert [(r.method, r.path) for r in transport.requests] == [
        ("GET", "/gmail/v1/users/me/history"),
        ("GET", "/gmail/v1/users/me/messages/18f0a1b2c3d4e5f6"),
        (
            "GET",
            "/gmail/v1/users/me/messages/18f0a1b2c3d4e5f6/attachments/ANGjdJ8fakeAttachmentId0",
        ),
    ]

    # -- the PDF landed in GCS, byte-for-byte -----------------------------
    # `.../{message_id}/{partId}/{filename}` -- part 0 is the text/plain body,
    # part 1 is the PDF. See storage.py for why the path is per-MIME-part.
    blob_path = "intake/18f0a1b2c3d4e5f6/1/bill.pdf"
    assert blob_path in gcs, f"nothing uploaded; bucket holds {sorted(gcs)}"
    stored, content_type = gcs[blob_path]
    assert stored == FIXTURE_BILL.read_bytes()
    assert content_type == "application/pdf"

    # -- the event ---------------------------------------------------------
    assert len(published) == 2
    topic, event = published[0]
    assert topic == "case.document.added"
    assert event["case_id"] == "case-18f0a1b2c3d4e5f6"
    assert event["gcs_uri"] == f"gs://{BUCKET}/{blob_path}"
    assert event["filename"] == "bill.pdf"
    assert event["gmail_message_id"] == "18f0a1b2c3d4e5f6"
    assert event["gmail_thread_id"] == "18f0a1b2c3d4e5f6"
    assert "Sutter Bay Hospitals" in event["raw_text"], (
        "raw_text must carry the PDF's real text -- agent-core's Reader reads "
        "documents/{doc_id}.raw_text and nothing else ever populates it for a "
        f"Gmail-sourced bill; got {event['raw_text'][:120]!r}"
    )

    # -- and the body the patient typed, as its own document ---------------
    body_path = "intake/18f0a1b2c3d4e5f6/body/message-body.txt"
    assert body_path in gcs, f"the email body was not stored; bucket holds {sorted(gcs)}"
    _, body_event = published[1]
    assert body_event["doc_type"] == pipeline.PATIENT_STATEMENT_TYPE, (
        "the body must be published as a patient_statement -- agent-core's factmerge "
        "excludes that type from INCOMING_DOC_TYPES precisely so nothing a patient SAYS "
        "can be merged into the case as if a document had PROVEN it"
    )
    assert body_event["case_id"] == event["case_id"], "the body belongs to the same case"
    assert body_event["doc_id"] != event["doc_id"]
    assert body_event["gcs_uri"] == f"gs://{BUCKET}/{body_path}"
    assert "Attaching the bill I got from the hospital" in body_event["raw_text"]
    assert "Sutter Bay Hospitals" not in body_event["raw_text"], (
        "the body event must carry the BODY, not the attachment's text -- publishing the "
        "PDF's own words as something the patient wrote would let a document's contents "
        "re-enter the pipeline as an unverified patient claim"
    )

    # -- the cursor advanced to the notification's historyId ---------------
    assert state.get_last_history_id() == "1000042"


def test_history_list_is_scoped_to_the_inbox_on_the_wire(one_bill_arrives, gcs, published):
    """REGRESSION (fixed today, re-proved here at the HTTP layer): `users.watch`
    filters which changes TRIGGER a push; `history.list` does not inherit that
    filter and must be given `labelId=INBOX` itself. Unfiltered, any draft,
    sent message or spam carrying a PDF is ingested as if a patient had emailed
    a bill in.

    The earlier regression test asserted on the kwargs handed to a fake service
    object. This one reads the query string that would have gone to
    gmail.googleapis.com, which additionally proves the parameter is spelled
    the way the discovery document's argmap accepts (`labelId`, singular --
    `labelIds` raises `TypeError` before any request is made).
    """
    transport = one_bill_arrives
    state.set_last_history_id("1000000")
    body = gmail_push_envelope(pubsub_message_id="pubsub-1", history_id=1000042)
    pipeline.process_gmail_push(*pubsub.decode_push_envelope(body))

    history_calls = transport.requests_to(r"^/gmail/v1/users/me/history$")
    assert history_calls, "history.list was never called"
    for call in history_calls:
        assert call.query.get("labelId") == ["INBOX"], (
            "history.list went out unfiltered -- drafts, sent mail and spam "
            f"will be ingested as emailed bills. query={call.query!r}"
        )
        assert call.query.get("historyTypes") == ["messageAdded"]
        assert call.query.get("startHistoryId") == ["1000000"]


def test_labelids_plural_is_rejected_by_the_real_argmap(one_bill_arrives):
    """The other half of the same defect: proof that the discovery document
    really does reject the plural spelling, so `labelId` is not merely a
    stylistic preference. This is the failure the live path would have hit on
    its very first push."""
    service = gmail_client._service()
    with pytest.raises(TypeError, match="labelIds"):
        service.users().history().list(userId="me", startHistoryId="1", labelIds=["INBOX"])


def test_nested_multipart_alternative_with_an_image_sibling(
    monkeypatch, credentials, gcs, published
):
    """A message whose body is `multipart/alternative` (plain + HTML) and which
    carries both a PNG and the PDF -- i.e. the PDF is two levels down and has a
    non-PDF sibling. The PNG must not be stored; the PDF must."""
    attachments: dict[str, bytes] = {}
    message = build_gmail_message(
        message_id="18f0deadbeef0001",
        thread_id="18f0deadbeef0001",
        history_id="2000042",
        attachments_out=attachments,
        with_alternative_body=True,
        extra_image=True,
    )
    # Guard the fixture itself: if `email` ever stops nesting this way the test
    # would quietly stop covering the nested case.
    assert message["payload"]["mimeType"] == "multipart/mixed"
    nested = [p["mimeType"] for p in message["payload"]["parts"]]
    assert "multipart/alternative" in nested, nested

    transport = install_transport(
        monkeypatch,
        GmailTransport(
            messages={message["id"]: message},
            attachments=attachments,
            history={
                "2000000": [
                    dict(
                        history_page(
                            record_id="2000041",
                            message_id=message["id"],
                            thread_id=message["threadId"],
                            current_history_id="2000042",
                        ),
                        _token=None,
                    )
                ]
            },
        ),
    )
    state.set_last_history_id("2000000")
    body = gmail_push_envelope(pubsub_message_id="pubsub-nested", history_id=2000042)
    result = pipeline.process_gmail_push(*pubsub.decode_push_envelope(body))

    # The PDF plus the body. The body here comes from the multipart/alternative
    # container this fixture nests -- proof that `extract_body_text` walks the
    # same tree `extract_pdf_attachments` does and finds the text/plain part
    # rather than the HTML sibling.
    assert result["documents_published"] == 2
    assert not transport.unrouted
    assert [event["filename"] for event in attachment_events(published)] == ["bill.pdf"]
    assert len(body_events(published)) == 1
    assert body_events(published)[0]["raw_text"].startswith("Attaching the bill"), (
        "the HTML alternative was preferred over the text/plain body the patient typed"
    )
    assert sorted(k for k in gcs if k.startswith("intake/")) == [
        "intake/18f0deadbeef0001/2/bill.pdf",
        "intake/18f0deadbeef0001/body/message-body.txt",
    ], "the PNG sibling was stored as if it were a bill"
    # The PNG was never fetched: only one attachments.get went out.
    assert len(transport.requests_to(r".*/attachments/.*")) == 1


def test_two_pdfs_in_one_message_each_become_their_own_event(
    monkeypatch, credentials, gcs, published
):
    """A bill and its itemised companion in one email. Two attachment fetches,
    two GCS objects, two events -- and both share one case id, because a Gmail
    thread is one case."""
    attachments: dict[str, bytes] = {}
    message = build_gmail_message(
        message_id="18f0deadbeef0002",
        thread_id="18f0deadbeef0002",
        history_id="3000042",
        attachments_out=attachments,
        pdf_names=("bill.pdf", "itemized_bill.pdf"),
    )
    transport = install_transport(
        monkeypatch,
        GmailTransport(
            messages={message["id"]: message},
            attachments=attachments,
            history={
                "3000000": [
                    dict(
                        history_page(
                            record_id="3000041",
                            message_id=message["id"],
                            thread_id=message["threadId"],
                            current_history_id="3000042",
                        ),
                        _token=None,
                    )
                ]
            },
        ),
    )
    state.set_last_history_id("3000000")
    pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-two", history_id=3000042)
        )
    )

    assert not transport.unrouted
    assert [event["filename"] for event in attachment_events(published)] == [
        "bill.pdf",
        "itemized_bill.pdf",
    ]
    assert len({event["case_id"] for _, event in published}) == 1
    assert len({event["doc_id"] for _, event in published}) == 3
    assert len(transport.requests_to(r".*/attachments/.*")) == 2


def test_attachment_is_fetched_by_attachment_id_never_read_inline(one_bill_arrives, gcs, published):
    """gmail.v1.json, `MessagePartBody`: an attachment's bytes are NOT in the
    `messages.get` response -- `data` is absent and `attachmentId` is present,
    and the bytes need a second `messages.attachments.get` round-trip. This
    pins that the pipeline really makes that second call and does not, say,
    quietly store an empty body.
    """
    transport = one_bill_arrives
    state.set_last_history_id("1000000")

    pdf_part = [
        p
        for p in transport.messages["18f0a1b2c3d4e5f6"]["payload"]["parts"]
        if p["filename"] == "bill.pdf"
    ][0]
    assert "data" not in pdf_part["body"], "fixture drift: Gmail never inlines attachments"
    assert pdf_part["body"]["attachmentId"]

    pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-att", history_id=1000042)
        )
    )

    fetches = transport.requests_to(r".*/attachments/.*")
    assert len(fetches) == 1
    assert fetches[0].path.endswith("/attachments/ANGjdJ8fakeAttachmentId0")
    assert gcs["intake/18f0a1b2c3d4e5f6/1/bill.pdf"][0] == FIXTURE_BILL.read_bytes()


def test_pagination_walks_every_history_page_and_filters_all_of_them(
    monkeypatch, credentials, gcs, published
):
    """`nextPageToken` is echoed back verbatim; the INBOX filter has to survive
    onto page 2 or it silently stops applying past the first 100 records."""
    attachments: dict[str, bytes] = {}
    first = build_gmail_message(
        message_id="18f0page000001",
        thread_id="18f0page000001",
        history_id="4000010",
        attachments_out=attachments,
    )
    second = build_gmail_message(
        message_id="18f0page000002",
        thread_id="18f0page000002",
        history_id="4000020",
        attachments_out=attachments,
        pdf_names=("second_bill.pdf",),
    )
    page_one = dict(
        history_page(
            record_id="4000010",
            message_id=first["id"],
            thread_id=first["threadId"],
            current_history_id="4000020",
        ),
        nextPageToken="ChgKFjA5MjM0NTY3ODkwMTIzNDU2Nzg5MA",
        _token=None,
    )
    page_two = dict(
        history_page(
            record_id="4000020",
            message_id=second["id"],
            thread_id=second["threadId"],
            current_history_id="4000020",
        ),
        _token="ChgKFjA5MjM0NTY3ODkwMTIzNDU2Nzg5MA",
    )
    transport = install_transport(
        monkeypatch,
        GmailTransport(
            messages={first["id"]: first, second["id"]: second},
            attachments=attachments,
            history={"4000000": [page_one, page_two]},
        ),
    )
    state.set_last_history_id("4000000")
    pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-page", history_id=4000020)
        )
    )

    assert not transport.unrouted, f"a page token was not echoed back: {transport.unrouted}"
    calls = transport.requests_to(r"^/gmail/v1/users/me/history$")
    assert [c.query.get("pageToken", [None])[0] for c in calls] == [
        None,
        "ChgKFjA5MjM0NTY3ODkwMTIzNDU2Nzg5MA",
    ]
    assert all(c.query.get("labelId") == ["INBOX"] for c in calls)
    assert [event["filename"] for event in attachment_events(published)] == [
        "bill.pdf",
        "second_bill.pdf",
    ]
    # One body per MESSAGE, and these are two messages on two threads.
    assert len(body_events(published)) == 2


# --------------------------------------------------------------------------
# The 404 / expired-cursor recovery, end to end
# --------------------------------------------------------------------------
def test_expired_cursor_404_rebootstraps_and_still_delivers_this_message(
    monkeypatch, credentials, gcs, published
):
    """The whole recovery path, over the wire.

    Gmail 404s the aged-out cursor (a REAL `googleapiclient.errors.HttpError`
    this time, not a lookalike -- so `_is_history_expired` is proved against
    the genuine exception). The pipeline must: re-arm the watch, rewrite the
    cursor to the FRESH historyId, and still deliver the message that triggered
    this very push by retrying with the notification's own historyId -- which
    is minutes old and therefore still inside the history window.
    """
    attachments: dict[str, bytes] = {}
    message = build_gmail_message(
        message_id="18f0expired0001",
        thread_id="18f0expired0001",
        history_id="5000042",
        attachments_out=attachments,
    )
    transport = install_transport(
        monkeypatch,
        GmailTransport(
            messages={message["id"]: message},
            attachments=attachments,
            history={
                "5000042": [
                    dict(
                        history_page(
                            record_id="5000042",
                            message_id=message["id"],
                            thread_id=message["threadId"],
                            current_history_id="5000042",
                        ),
                        _token=None,
                    )
                ]
            },
            expired_history_ids=frozenset({"4000000"}),
            watch_responses=[{"historyId": "5000100", "expiration": "1756829400000"}],
        ),
    )
    state.set_last_history_id("4000000")

    result = pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-expired", history_id=5000042)
        )
    )

    assert result["status"] == "history_expired_rebootstrapped"
    assert result["documents_published"] == 2, (
        "the message that triggered this push was dropped by the recovery -- "
        "its own historyId is minutes old and still inside the window "
        "(two documents: the PDF and the email body)"
    )
    assert result["new_history_id"] == "5000100"
    assert not transport.unrouted

    # The watch really was re-armed, with the right topic and label filter.
    watch_calls = transport.requests_to(r"^/gmail/v1/users/me/watch$")
    assert len(watch_calls) == 1
    assert watch_calls[0].body == {
        "topicName": f"projects/{PROJECT}/topics/intake.email.received",
        "labelIds": ["INBOX"],
    }

    # The cursor moved FORWARD to the watch's fresh id, never backwards onto
    # the stale notification value.
    assert state.get_last_history_id() == "5000100"
    assert published and published[0][1]["gmail_message_id"] == "18f0expired0001"


def test_a_500_is_not_mistaken_for_an_expired_cursor(monkeypatch, credentials, gcs, published):
    """A backend error is transient and must propagate: re-arming the watch on
    a blip throws away a good cursor and skips everything still recoverable
    from it. Proved against a real `HttpError`, whose `status_code` is a
    property over `resp.status`."""

    class _Failing(GmailTransport):
        def _history_list(self, req, user_id):
            return 500, _error_body(500)

    transport = install_transport(
        monkeypatch, _Failing(messages={}, attachments={}, history={"6000000": []})
    )
    state.set_last_history_id("6000000")

    with pytest.raises(HttpError) as exc:
        pipeline.process_gmail_push(
            *pubsub.decode_push_envelope(
                gmail_push_envelope(pubsub_message_id="pubsub-500", history_id=6000042)
            )
        )
    assert exc.value.status_code == 500
    assert not transport.requests_to(r"^/gmail/v1/users/me/watch$"), (
        "a transient 500 must not burn the watch"
    )
    assert published == []


def test_watch_refused_by_gmail_surfaces_the_real_error_body(monkeypatch, credentials, gcs):
    """The most likely live failure: Gmail refuses `users.watch` because
    `gmail-api-push@system.gserviceaccount.com` has no `pubsub.publisher` on
    the topic. It must raise with Google's message intact, not a bare
    `Exception` -- `verify_live.sh` step 2 prints exactly this body.
    """
    transport = install_transport(
        monkeypatch,
        GmailTransport(messages={}, attachments={}, history={}, watch_responses=[]),
    )
    with pytest.raises(HttpError) as exc:
        gmail_client.start_watch()
    assert exc.value.status_code == 403
    assert "Cloud PubSub" in str(exc.value)
    assert transport.requests_to(r"^/gmail/v1/users/me/watch$")


# --------------------------------------------------------------------------
# Redelivery
# --------------------------------------------------------------------------
def test_redelivering_the_same_push_publishes_nothing_twice(one_bill_arrives, gcs, published):
    """Agreement §2.3. Pub/Sub redelivers with the same `messageId`; the second
    delivery must be a no-op that still 200s -- and must not re-fetch anything
    from Gmail either."""
    transport = one_bill_arrives
    state.set_last_history_id("1000000")
    body = gmail_push_envelope(pubsub_message_id="pubsub-dup", history_id=1000042)

    first = pipeline.process_gmail_push(*pubsub.decode_push_envelope(body))
    calls_after_first = len(transport.requests)
    second = pipeline.process_gmail_push(*pubsub.decode_push_envelope(body))

    assert first["status"] == "ok"
    assert second == {"status": "duplicate", "message_id": "pubsub-dup"}
    assert len(published) == 2, "the redelivery published a second event"
    assert len(transport.requests) == calls_after_first, "the redelivery hit Gmail again"


def test_a_second_notification_for_an_already_stored_attachment_is_a_no_op(
    one_bill_arrives, gcs, published
):
    """The other redelivery shape: a DIFFERENT Pub/Sub message (so
    `gmail_push` dedupe does not fire) that reports the same Gmail message --
    two overlapping `history.list` windows, which really happens. The
    per-attachment claim is what stops the double publish."""
    transport = one_bill_arrives
    state.set_last_history_id("1000000")

    pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-a", history_id=1000042)
        )
    )
    state.set_last_history_id("1000000")  # overlapping window: same cursor again
    result = pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-b", history_id=1000042)
        )
    )

    assert result["status"] == "ok"
    assert result["documents_published"] == 0
    # The BODY is claimed the same way an attachment is, so the second
    # notification re-publishes neither. A body that slipped through the claim
    # would re-run Reader's extraction over the same prose and re-assert the
    # patient's statements as if they had written twice -- which
    # `statedfacts.collect` would then report as two statements that agree.
    assert len(published) == 2
    assert len(body_events(published)) == 1
    assert len(transport.requests_to(r".*/attachments/.*")) == 1


def test_a_failed_publish_releases_the_claim_so_the_retry_really_retries(
    one_bill_arrives, gcs, monkeypatch
):
    """The provisional-claim contract, end to end: if the publish fails after
    the claim is taken, the redelivery must genuinely reprocess rather than be
    waved through as a duplicate. This is the shape of defect #2 -- a
    traceback followed by a clean 200 over a dropped bill."""
    transport = one_bill_arrives
    calls = {"n": 0}
    captured: list[dict] = []

    def _flaky_publish(topic: str, data: dict):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Deadline Exceeded publishing to case.document.added")
        captured.append(data)
        return "pubsub-msg-1"

    monkeypatch.setattr(pubsub, "publish", _flaky_publish)
    state.set_last_history_id("1000000")

    with pytest.raises(RuntimeError):
        pipeline.process_gmail_push(
            *pubsub.decode_push_envelope(
                gmail_push_envelope(pubsub_message_id="pubsub-flaky", history_id=1000042)
            )
        )
    # Both claims must be gone: the push's and the attachment's.
    assert not [k for k in gcs if k.startswith("_dedupe/")], (
        f"claims survived a failure: {[k for k in gcs if k.startswith('_dedupe/')]}"
    )

    result = pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-flaky", history_id=1000042)
        )
    )
    assert result["documents_published"] == 2
    assert len(captured) == 2
    assert not transport.unrouted


# --------------------------------------------------------------------------
# The consumer contract -- defect #7's exact shape
# --------------------------------------------------------------------------
def _payload_keys_read_by(source: Path, function_name: str) -> tuple[set[str], set[str]]:
    """Every literal key read off a dict named `payload` inside one function,
    split into `(required, optional)`.

    Reads the CONSUMER's source rather than importing it: `services/agent-core`
    is a separate Cloud Run service with its own dependency set (Firestore,
    ADK, Gemini), and importing it from this suite would couple two services'
    test environments together for no gain. AST is exact where a `grep` would
    not be.

    THE SPLIT IS THE CONSUMER'S OWN DECLARATION, not a list this suite wrote
    down. `payload["case_id"]` raises if the key is absent, so agent-core is
    saying that key must be on every message; `payload.get("doc_type")` has a
    default, so agent-core is saying it may or may not be there. That
    distinction became load-bearing the moment intake started publishing two
    KINDS of document event: an attachment carries no `doc_type` (its type is
    Gemma's to decide) and the email body carries exactly one. Asserting every
    consumed key on every event would have forced intake to stamp a type on
    documents whose type is a real, unanswered question -- the test demanding
    the bug.
    """
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
            required: set[str] = set()
            optional: set[str] = set()
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "payload"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                    and isinstance(inner.args[0].value, str)
                ):
                    optional.add(inner.args[0].value)
                if (
                    isinstance(inner, ast.Subscript)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "payload"
                    and isinstance(inner.slice, ast.Constant)
                    and isinstance(inner.slice.value, str)
                ):
                    required.add(inner.slice.value)
            return required, optional - required
    raise AssertionError(f"{function_name} not found in {source}")


def test_the_event_carries_every_field_the_consumer_actually_reads(
    one_bill_arrives, gcs, published
):
    """DEFECT #7, VERBATIM, IS A FIELD-NAME MISMATCH ACROSS THIS EXACT SEAM.

    Every letter this project sent went out blank because the templates read
    `patient["first_name"]` and the contract has no such field. Nothing failed;
    the letters were simply empty. The same seam exists here: `services/intake`
    publishes `case.document.added` and `services/agent-core` consumes it, and
    the two are developed by different personas in different directories.

    So this test does not assert against a list RELAY wrote down. It parses the
    consumer's own source and asserts that every key agent-core reads off the
    payload is a key intake put there. If either side is renamed, this fails on
    the next run instead of on camera.
    """
    state.set_last_history_id("1000000")
    pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="pubsub-contract", history_id=1000042)
        )
    )
    assert published, "nothing was published, so there is no payload to check"
    events = [event for _, event in published]

    agent_core = REPO_ROOT / "services/agent-core"
    assert agent_core.is_dir(), (
        f"{agent_core} is missing -- this test exists to compare intake's published "
        "payload against the consumer's real source and cannot be skipped away"
    )

    pipeline_required, pipeline_optional = _payload_keys_read_by(
        agent_core / "agent_core/pipeline.py", "ensure_case_and_document_from_event"
    )
    main_required, main_optional = _payload_keys_read_by(
        agent_core / "main.py", "pubsub_document_added"
    )
    required = pipeline_required | main_required
    optional = (pipeline_optional | main_optional) - required

    # Required keys: on EVERY event, no exceptions. These are the ones
    # agent-core subscripts, so an absent one is a KeyError mid-cascade.
    for event in events:
        missing = required - set(event)
        assert not missing, (
            "services/agent-core reads keys off `case.document.added` that "
            f"services/intake never publishes: {sorted(missing)}. Published keys: "
            f"{sorted(event)}. This is defect #7's exact shape."
        )

    # Optional keys: agent-core has a default for each, but a key NO event
    # ever carries is still a seam where one side was renamed and the other
    # silently degraded to its default -- defect #7 with a softer landing.
    never_sent = optional - set().union(*(set(e) for e in events))
    assert not never_sent, (
        "services/agent-core reads optional keys off `case.document.added` that no "
        f"intake event carries at all: {sorted(never_sent)}. Either intake stopped "
        "publishing them or one side was renamed; agent-core is quietly falling back to "
        "its defaults."
    )

    # And the gate agent-core puts in front of auto-creation: an event that
    # carries none of these is acked and thrown away.
    tree = ast.parse((agent_core / "agent_core/pipeline.py").read_text())
    document_fields: tuple[str, ...] | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "EVENT_DOCUMENT_FIELDS" for t in node.targets
            )
            and isinstance(node.value, ast.Tuple)
        ):
            document_fields = tuple(e.value for e in node.value.elts if isinstance(e, ast.Constant))
    assert document_fields, "agent-core's EVENT_DOCUMENT_FIELDS could not be read"
    assert set(document_fields) & set(event), (
        f"the event carries none of {document_fields}, so agent-core's "
        "document-added handler acks it and creates nothing -- an emailed bill "
        "would land in GCS and produce no case at all"
    )


# --------------------------------------------------------------------------
# The fixtures' own provenance
# --------------------------------------------------------------------------
def test_discovery_document_still_pins_these_shapes():
    """Re-reads the pinned wheel's discovery document and checks every claim
    this module's docstring makes about it. If google-api-python-client is
    bumped and Gmail's surface moved, this fails rather than letting the
    fixtures drift into fiction.
    """
    doc_path = (
        Path(googleapiclient_discovery.__file__).parent / "discovery_cache/documents/gmail.v1.json"
    )
    assert doc_path.exists(), (
        f"{doc_path} is missing -- the fixtures in this module are derived from it"
    )
    doc = json.loads(doc_path.read_text())
    users = doc["resources"]["users"]

    history_list = users["resources"]["history"]["methods"]["list"]
    assert history_list["httpMethod"] == "GET"
    assert history_list["path"] == "gmail/v1/users/{userId}/history"
    assert "labelId" in history_list["parameters"], (
        "the INBOX filter parameter is gone from Gmail v1; the regression fix "
        "in gmail_client.list_new_message_ids needs revisiting"
    )
    assert "labelIds" not in history_list["parameters"]
    assert history_list["parameters"]["historyTypes"].get("repeated") is True
    assert "HTTP 404" in history_list["parameters"]["startHistoryId"]["description"]

    messages_get = users["resources"]["messages"]["methods"]["get"]
    assert messages_get["path"] == "gmail/v1/users/{userId}/messages/{id}"
    assert "full" in messages_get["parameters"]["format"]["enum"]

    attachments_get = users["resources"]["messages"]["resources"]["attachments"]["methods"]["get"]
    assert (
        attachments_get["path"] == "gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}"
    )

    assert users["methods"]["watch"]["httpMethod"] == "POST"
    for field in ("attachmentId", "data", "size"):
        assert field in doc["schemas"]["MessagePartBody"]["properties"]
    for field in ("body", "filename", "headers", "mimeType", "partId", "parts"):
        assert field in doc["schemas"]["MessagePart"]["properties"]
    for field in ("history", "historyId", "nextPageToken"):
        assert field in doc["schemas"]["ListHistoryResponse"]["properties"]
    assert "messagesAdded" in doc["schemas"]["History"]["properties"]
    for field in ("historyId", "expiration"):
        assert field in doc["schemas"]["WatchResponse"]["properties"]
    for field in ("topicName", "labelIds"):
        assert field in doc["schemas"]["WatchRequest"]["properties"]


def test_two_attachments_sharing_a_filename_are_both_delivered(
    monkeypatch, credentials, gcs, published
):
    """DEFECT FOUND BY THIS HARNESS. One email, two PDFs, both called
    `bill.pdf` -- two pages from the same scanner app, a bill and a re-send, a
    forward that re-attaches. Ordinary mail.

    Before the fix, the dedupe claim and the GCS object path were both keyed on
    `(message_id, filename)`, so the SECOND attachment was claimed as an
    already-seen duplicate: no `case.document.added`, no GCS object, no error,
    no log line, and `{"status": "ok", "documents_published": 1}` returned to
    Pub/Sub. A legal document disappeared and the handler reported success --
    HANDOFF.md's bug pattern verbatim. Fifty-three passing unit tests never
    went near it, because every one of them fakes a message with a single
    attachment.

    Pre-fix this test fails with:
        assert ['bill.pdf'] == ['bill.pdf', 'bill.pdf']
    """
    attachments: dict[str, bytes] = {}
    message = build_gmail_message(
        message_id="18f0probe0001",
        thread_id="18f0probe0001",
        history_id="7000042",
        attachments_out=attachments,
        pdf_names=("bill.pdf", "bill.pdf"),
    )
    transport = install_transport(
        monkeypatch,
        GmailTransport(
            messages={message["id"]: message},
            attachments=attachments,
            history={
                "7000000": [
                    dict(
                        history_page(
                            record_id="7000041",
                            message_id=message["id"],
                            thread_id=message["threadId"],
                            current_history_id="7000042",
                        ),
                        _token=None,
                    )
                ]
            },
        ),
    )
    state.set_last_history_id("7000000")
    result = pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="probe-1", history_id=7000042)
        )
    )
    assert [event["filename"] for event in attachment_events(published)] == [
        "bill.pdf",
        "bill.pdf",
    ], (
        "an attachment was dropped: two PDFs sharing a filename must both be "
        "delivered, not silently deduplicated against each other"
    )
    assert result["documents_published"] == 3  # two PDFs + the email body
    assert len(transport.requests_to(r".*/attachments/.*")) == 2

    # Two distinct GCS objects, and neither overwrote the other. The path
    # segment between the message id and the filename is the MIME `partId`.
    stored = sorted(k for k in gcs if k.startswith("intake/"))
    assert stored == [
        "intake/18f0probe0001/1/bill.pdf",
        "intake/18f0probe0001/2/bill.pdf",
        "intake/18f0probe0001/body/message-body.txt",
    ], stored
    assert len({event["doc_id"] for _, event in published}) == 3
    assert len({event["gcs_uri"] for _, event in published}) == 3

    # ...and a redelivery still deduplicates BOTH of them, i.e. the fix did not
    # buy uniqueness by breaking idempotency (§2.3).
    state.set_last_history_id("7000000")
    again = pipeline.process_gmail_push(
        *pubsub.decode_push_envelope(
            gmail_push_envelope(pubsub_message_id="probe-2", history_id=7000042)
        )
    )
    assert again["documents_published"] == 0
    assert len(published) == 3


def test_the_fixture_bill_is_the_repos_own_synthetic_pdf():
    """Playbook §0.6: synthetic data only. The bytes this suite pushes through
    Gmail are the same committed fixture the demo uses, not an invented file."""
    assert FIXTURE_BILL.exists(), f"{FIXTURE_BILL} is missing -- run fixtures/build.py"
    raw = FIXTURE_BILL.read_bytes()
    assert raw.startswith(b"%PDF-")
    assert len(raw) > 1000
