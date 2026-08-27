"""Gmail API wrapper -- watch, history, message + attachment fetch.

Authenticates as the demo Gmail account via this service's own
`google_auth.py` (a deliberate standalone copy of
`packages/delivery/delivery/google_auth.py` -- see that module's docstring
for why). `googleapiclient` is imported lazily inside `_service()` for the
same "don't break test collection for environments without it installed"
reason as `packages/delivery`.
"""

from __future__ import annotations

import base64
import os

from . import state, text_extract
from .google_auth import load_user_credentials

# read-only is sufficient: watching for new mail and reading messages/
# attachments never needs write access (least privilege).
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _service():
    creds = load_user_credentials(GMAIL_SCOPES)
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds)


def start_watch(topic_name: str | None = None) -> dict:
    """`users.watch` -- Gmail pushes new-message notifications to our topic
    for 7 days from this call. WO1: "Handle the 7-day watch renewal with a
    Cloud Scheduler job" -- this is the function that job's HTTP hit
    (`POST /gmail/watch/renew` in `main.py`) calls.
    """
    service = _service()
    topic = topic_name or (
        f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}/topics/"
        f"{os.environ.get('TOPIC_INTAKE_EMAIL_RECEIVED', 'intake.email.received')}"
    )
    result = (
        service.users()
        .watch(userId="me", body={"topicName": topic, "labelIds": ["INBOX"]})
        .execute()
    )
    if result.get("historyId"):
        state.set_last_history_id(str(result["historyId"]))
    return result


class HistoryExpired(Exception):
    """`startHistoryId` is too old for Gmail to diff against.

    Gmail keeps the history log for a limited window and answers **404** for
    any `startHistoryId` that has aged out of it. Verbatim from the Gmail v1
    discovery document shipped inside google-api-python-client 2.199.0
    (`users.history.list`, parameter `startHistoryId`):

        "Supplying an invalid or out of date `startHistoryId` typically
        returns an `HTTP 404` error code. A `historyId` is typically valid
        for at least a week, but in some rare circumstances may be valid for
        only a few hours. If you receive an `HTTP 404` error response, your
        application should perform a full sync."

    The cursor in `state.py` is therefore perishable: a quiet weekend, a
    paused demo, or "in some rare circumstances" a few HOURS invalidates it.

    Raised instead of leaking `googleapiclient`'s `HttpError` so the recovery
    policy lives in `pipeline.py` (which owns the cursor and knows what
    "start over from here" means) rather than in this module, whose job is to
    be a thin API wrapper.
    """


def _is_history_expired(exc: Exception) -> bool:
    """True for the 404 Gmail returns on an aged-out `startHistoryId`.

    Reads both attributes `googleapiclient.errors.HttpError` exposes rather
    than picking one. Read out of the pinned 2.199.0 wheel's `errors.py`, not
    assumed: `HttpError.__init__` sets `self.resp`, and `status_code` is a
    `@property` returning `self.resp.status` -- so on the real exception the
    two can never disagree. The fallback earns its keep against anything that
    is only HttpError-SHAPED (a stub, a mock, a future rename).

    Deliberately duck-typed rather than `except HttpError`: `googleapiclient`
    is a real dependency of this service but is not installed in every
    environment that collects these tests, and a module-level import of it
    here would trade a runtime bug for a collection-time one.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) == 404
    except (TypeError, ValueError):
        return False


def list_new_message_ids(start_history_id: str) -> list[str]:
    """Every INBOX message added since `start_history_id`, across paginated
    results.

    `labelId="INBOX"` is load-bearing. `start_watch` above passes
    `labelIds: ["INBOX"]`, but that only constrains which changes TRIGGER a
    push notification -- it is not remembered by, and does not propagate to,
    `history.list`. Unfiltered, `history.list` reports `messageAdded` across
    EVERY label, so a draft, a sent message, or a spam message that happens
    to carry a PDF is fetched, stored to GCS and published as
    `case.document.added` exactly as if a patient had emailed a bill in. On a
    live demo account that is any PDF the operator touches, on camera.

    `labelId` (singular, a plain string) is the real parameter name -- read
    off the Gmail v1 discovery document in google-api-python-client 2.199.0,
    where it is documented as "Only return messages with a label matching the
    ID." Passing the `labelIds` list that `users.watch` takes instead would
    raise `TypeError: Got an unexpected keyword argument labelIds` at the
    first push -- `discovery.py` validates kwargs against the method's
    argmap. Loud, at least, but only at runtime, and this code path has never
    run against a live Gmail (no OAuth token has ever been minted).

    HANDOFF (not built here): a From-header allowlist would narrow this
    further, since INBOX still admits anything anyone sends the demo account.
    It is not a one-liner -- it needs header extraction, RFC-5322 address
    normalisation, and an explicit "unset means allow all" default so an
    empty env var cannot silently swallow the demo bill. Written up in the PR
    description rather than smuggled into this change.

    Raises `HistoryExpired` if Gmail 404s because the cursor has aged out of
    the history window; see that class and `pipeline.process_gmail_push`.
    """
    service = _service()
    message_ids: list[str] = []
    page_token = None
    while True:
        try:
            resp = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                    pageToken=page_token,
                )
                .execute()
            )
        except Exception as exc:
            if _is_history_expired(exc):
                raise HistoryExpired(
                    f"Gmail returned 404 for startHistoryId={start_history_id!r} -- "
                    "the cursor has aged out of the history window"
                ) from exc
            raise
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                message_ids.append(added["message"]["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def fetch_message(message_id: str) -> dict:
    service = _service()
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def extract_pdf_attachments(message: dict) -> list[dict]:
    """Walk a message's MIME tree; return PDF parts as
    `[{"filename", "attachment_id", "mime_type", "part_id"}, ...]`.

    WO1 scopes this to PDF bills specifically ("stores raw attachments (PDF
    bills) to GCS") -- a non-PDF attachment (e.g. an inline signature image)
    is intentionally skipped rather than stored, to keep the intake surface
    matching what the rest of the pipeline expects to classify.

    `part_id` IS LOAD-BEARING, not decoration. Nothing else about an
    attachment is unique within a message:

    * `filename` is not. One email can carry two PDFs both called `bill.pdf`
      or `scan.pdf` -- two pages exported by the same scanner app, a bill and
      a re-send, a forward that re-attaches. Before this field existed, the
      pipeline keyed both its dedupe claim and its GCS object path on
      `(message_id, filename)`, so the SECOND such attachment was claimed as
      an already-seen duplicate and dropped: no event, no GCS object, no
      error, no log line. A legal document vanished and the handler returned
      `{"status": "ok"}`. That is HANDOFF.md's bug pattern exactly -- reported
      success while doing nothing -- and it was found by the transport-level
      harness in `tests/test_gmail_transport.py`, not by the 53 tests that
      passed over it.
    * `attachmentId` is unique but NOT stable: Gmail's own reference describes
      it only as an id "that can be retrieved in a separate
      `messages.attachments.get` request", with no stability guarantee across
      calls, and it is known to differ between two `messages.get` responses
      for the same message. Keying dedupe on it would turn a redelivery into
      a duplicate publish -- trading a silent drop for a silent double.

    `partId` is the one identifier Gmail documents as stable: "The immutable
    ID of the message part" (`MessagePart.partId`, Gmail v1 discovery
    document, google-api-python-client 2.199.0). It is unique within a
    message and identical on every fetch of it.

    The walk position (`"0"`, `"1"`, `"1.0"`, ...) is used as a fallback if a
    response ever omits `partId`, so this never depends on a field being
    present -- the fallback reproduces the same numbering Gmail itself uses.
    """
    out: list[dict] = []

    def _is_pdf(filename: str, mime_type: str) -> bool:
        return filename.lower().endswith(".pdf") or mime_type == "application/pdf"

    def walk(parts: list[dict], prefix: str = "") -> None:
        for index, part in enumerate(parts or []):
            part_id = part.get("partId") or f"{prefix}{index}"
            filename = part.get("filename") or ""
            body = part.get("body", {})
            mime_type = part.get("mimeType", "")
            if filename and body.get("attachmentId") and _is_pdf(filename, mime_type):
                out.append(
                    {
                        "filename": filename,
                        "attachment_id": body["attachmentId"],
                        "mime_type": mime_type or "application/pdf",
                        "part_id": part_id,
                    }
                )
            if part.get("parts"):
                walk(part["parts"], prefix=f"{part_id}.")

    payload = message.get("payload", {})
    walk(payload.get("parts", []))
    # Single-part messages carry the attachment directly on the payload, whose
    # own `partId` is `""` -- the fallback numbering gives it `"0"`.
    if (
        not payload.get("parts")
        and payload.get("filename")
        and payload.get("body", {}).get("attachmentId")
    ):
        walk([payload])
    return out


def _decode_body_data(data: str) -> str:
    """Gmail's URL-safe base64 body payload -> text. `""` on anything that
    does not decode, never an exception: a single mis-encoded part must not
    cost a patient the attachments that arrived in the same email.

    `validate=True`, unlike `fetch_attachment_bytes` below. Without it,
    `b64decode` silently DISCARDS every character outside the alphabet and
    decodes whatever is left -- so a corrupt or wrongly-typed body part comes
    back as a short run of mojibake rather than as an error, and that mojibake
    would be stored to GCS and published as prose a patient wrote. An
    extraction over garbage is exactly the input that invites a model to
    compose something plausible. Whitespace is stripped first because it is
    the one thing a transport may legitimately insert into a base64 payload.
    """
    try:
        compact = "".join(data.split())
        padded = compact + "=" * (-len(compact) % 4)
        # `b64decode(altchars=...)`, not `urlsafe_b64decode`, only because the
        # urlsafe wrapper does not expose `validate`.
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        return raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 -- a bad body part must not take down intake
        return ""


def extract_body_text(message: dict) -> str:
    """The human-written body of the email, as plain text. `""` if it has none.

    WHY THIS EXISTS. Everything this system knew about a case came off a PDF,
    and the one fact no PDF can carry is household size: a pay stub states an
    employee's earnings, never who else lives in their home (see
    `agent_core.factmerge.UNSOURCEABLE_PATIENT_FACTS`). Patients state it in
    the covering email -- "Household of three, I make about $32,000 a year" --
    and this service threw that sentence away. It is not evidence in the sense
    a document is, and the pipeline treats it as strictly weaker (see the
    `patient_statement` document type in `agent_core.factmerge`); but
    discarding it entirely means the one input that decides whether a $2,625
    bill is erased can never reach the system at all.

    Prefers `text/plain` and falls back to a de-tagged `text/html`, both
    walked depth-first: multipart/alternative carries the same body twice and
    the plain part is the one the patient actually typed. A part with a
    `filename` is an attachment, not the body, and is skipped -- that is
    `extract_pdf_attachments`'s job and double-counting it here would publish
    a bill's text a second time as if the patient had written it.

    NOT DE-QUOTED. A reply carries the quoted thread beneath it, and stripping
    that is a heuristic ("On ... wrote:" in a dozen locales) that can silently
    eat real prose. The downstream extraction is grounded on a verbatim quote
    that must appear in this text (`agent_core.agents.reader`), so extra text
    can only ever cost tokens -- never manufacture a fact.
    """
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        if part.get("filename"):
            return
        mime = (part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data")
        if data:
            if mime.startswith("text/plain"):
                plain.append(_decode_body_data(data))
            elif mime.startswith("text/html"):
                html.append(_decode_body_data(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(message.get("payload") or {})
    for candidate in plain:
        if candidate.strip():
            return candidate.strip()[: text_extract.MAX_CHARS]
    for candidate in html:
        converted = text_extract.html_to_text(candidate)
        if converted:
            return converted
    return ""


def fetch_attachment_bytes(message_id: str, attachment_id: str) -> bytes:
    service = _service()
    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    # Gmail returns URL-safe base64 without guaranteed padding.
    padded = att["data"] + "=" * (-len(att["data"]) % 4)
    return base64.urlsafe_b64decode(padded)
