"""The email BODY as a document -- `intake.gmail_client.extract_body_text`
and the `patient_statement` publish it feeds.

WHY THE BODY MATTERS AT ALL. Everything this system knew about a case came
off a PDF, and the one fact no PDF can carry is household size -- a pay stub
states an employee's earnings, never who else lives in their home. Patients
put it in the covering email ("Household of three, I make about $32,000 a
year") and this service discarded it, so a $2,625 bill that California law
would erase entirely was worth $210 of duplicate-billing findings instead.

WHAT THIS SUITE GUARDS. That the body travels as its own document with its
own dedupe claim and its own GCS object; that it is published as
`patient_statement` and nothing else, so `agent_core.factmerge` can keep it
out of the canonical facts by construction; and that the intake surface does
not widen -- a message with no attachment still produces nothing at all.
"""

from __future__ import annotations

import base64

from intake import gmail_client, pipeline, text_extract


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


BODY = "I'm uninsured and paying out of pocket. Household of three, I make about $32,000 a year."


def _message(*, parts=None, payload=None, thread_id="thread_1") -> dict:
    return {
        "threadId": thread_id,
        "payload": payload if payload is not None else {"parts": parts or []},
    }


def _pdf_part(part_id="1", filename="bill.pdf"):
    return {
        "partId": part_id,
        "mimeType": "application/pdf",
        "filename": filename,
        "body": {"attachmentId": "att_1"},
    }


def _text_part(text, mime="text/plain", part_id="0"):
    return {"partId": part_id, "mimeType": mime, "filename": "", "body": {"data": _b64(text)}}


# --------------------------------------------------------------------------
# extract_body_text
# --------------------------------------------------------------------------
def test_a_plain_text_body_is_returned_verbatim():
    message = _message(parts=[_text_part(BODY), _pdf_part()])
    assert gmail_client.extract_body_text(message) == BODY


def test_the_plain_text_alternative_is_preferred_over_the_html_one():
    """multipart/alternative carries the same body twice. The plain part is
    what the patient typed; the HTML one is their client's rendering of it,
    and a verbatim quote has to come from the version they actually wrote."""
    message = _message(
        parts=[
            {
                "partId": "0",
                "mimeType": "multipart/alternative",
                "parts": [
                    _text_part(BODY, part_id="0.0"),
                    _text_part(f"<p>{BODY}</p>", mime="text/html", part_id="0.1"),
                ],
            },
            _pdf_part(part_id="1"),
        ]
    )
    assert gmail_client.extract_body_text(message) == BODY


def test_an_html_only_body_is_de_tagged():
    message = _message(
        parts=[
            _text_part(
                "<div>Household of three,</div><p>I make about $32,000 &amp; no more.</p>",
                mime="text/html",
            )
        ]
    )
    out = gmail_client.extract_body_text(message)
    assert "<div>" not in out and "<p>" not in out
    assert "Household of three," in out
    assert "$32,000 & no more." in out


def test_script_and_style_bodies_are_dropped_not_de_tagged():
    message = _message(
        parts=[
            _text_part(
                "<style>p{color:red}</style><script>var x=1</script><p>Household of three</p>",
                mime="text/html",
            )
        ]
    )
    out = gmail_client.extract_body_text(message)
    assert "color:red" not in out and "var x" not in out
    assert "Household of three" in out


def test_an_attachment_part_is_never_read_as_the_body():
    """A part with a filename is an attachment. Publishing a bill's own text a
    second time as something "the patient wrote" would let a document's
    contents re-enter the pipeline as an unverified claim."""
    part = _text_part("TOTAL: $2,625.00")
    part["filename"] = "bill.txt"
    assert gmail_client.extract_body_text(_message(parts=[part])) == ""


def test_a_single_part_message_body_is_found():
    payload = {"partId": "", "mimeType": "text/plain", "filename": "", "body": {"data": _b64(BODY)}}
    assert gmail_client.extract_body_text(_message(payload=payload)) == BODY


def test_a_message_with_no_body_at_all_yields_the_empty_string():
    assert gmail_client.extract_body_text(_message(parts=[_pdf_part()])) == ""


def test_undecodable_body_data_degrades_to_empty_rather_than_raising():
    """One mis-encoded MIME part must not cost the patient the attachments
    that arrived in the same email."""
    part = {"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"data": "!!!not b64"}}
    assert gmail_client.extract_body_text(_message(parts=[part])) == ""


def test_a_very_long_body_is_clipped_rather_than_publishing_an_oversized_event():
    message = _message(parts=[_text_part("x" * (text_extract.MAX_CHARS + 5_000))])
    assert len(gmail_client.extract_body_text(message)) == text_extract.MAX_CHARS


# --------------------------------------------------------------------------
# the publish
# --------------------------------------------------------------------------
def _run(monkeypatch, message, *, claims=None):
    published: list[tuple] = []
    uploads: list[tuple] = []
    monkeypatch.setattr(gmail_client, "fetch_message", lambda mid: message)
    monkeypatch.setattr(pipeline.gmail_client, "fetch_message", lambda mid: message)
    monkeypatch.setattr(
        pipeline.gmail_client, "fetch_attachment_bytes", lambda mid, aid: b"%PDF fake"
    )
    monkeypatch.setattr(
        pipeline.dedupe, "claim", claims if claims is not None else (lambda ns, key: True)
    )
    monkeypatch.setattr(
        pipeline.storage,
        "upload_attachment",
        lambda mid, part_id, filename, content, ct: (
            uploads.append((mid, part_id, filename, content, ct))
            or f"gs://bucket/intake/{mid}/{part_id}/{filename}"
        ),
    )
    monkeypatch.setattr(
        pipeline.pubsub, "publish", lambda topic, data: published.append((topic, data))
    )
    return pipeline.process_new_message("msg_1"), published, uploads


def test_the_body_is_published_as_a_patient_statement(monkeypatch):
    message = _message(parts=[_text_part(BODY), _pdf_part()])
    result, published, uploads = _run(monkeypatch, message)

    assert len(result) == 2
    body_event = result[-1]
    assert body_event["doc_type"] == pipeline.PATIENT_STATEMENT_TYPE
    assert body_event["raw_text"] == BODY
    assert body_event["case_id"] == "case-thread_1"
    assert body_event["filename"] == pipeline.BODY_FILENAME
    assert body_event["gcs_uri"] == "gs://bucket/intake/msg_1/body/message-body.txt"
    assert body_event["doc_id"] != result[0]["doc_id"]
    assert [topic for topic, _ in published] == [
        pipeline.TOPIC_CASE_DOCUMENT_ADDED,
        pipeline.TOPIC_CASE_DOCUMENT_ADDED,
    ]
    # Stored as bytes a human can open, like every other intake artifact.
    assert uploads[-1][3] == BODY.encode("utf-8")
    assert uploads[-1][4].startswith("text/plain")


def test_the_attachment_event_never_declares_a_type(monkeypatch):
    """A PDF's type is Gemma's to decide (§1.3's bonus model). Only the body
    -- whose "type" is a fact about which MIME part it came from, not a
    judgement about content -- may be declared."""
    message = _message(parts=[_text_part(BODY), _pdf_part()])
    result, _, _ = _run(monkeypatch, message)
    assert "doc_type" not in result[0]


def test_a_message_with_no_attachment_publishes_nothing_at_all(monkeypatch):
    """THE INTAKE SURFACE MUST NOT WIDEN. `history.list` is scoped to INBOX,
    which still admits every newsletter and reply-all the demo account
    receives. Publishing a body unconditionally would open a case per email,
    live, on camera."""
    message = _message(parts=[_text_part("Just checking in, no bill attached.")])
    result, published, _ = _run(monkeypatch, message)
    assert result == []
    assert published == []


def test_an_empty_body_publishes_no_second_document(monkeypatch):
    message = _message(parts=[_text_part("   \n  "), _pdf_part()])
    result, _, _ = _run(monkeypatch, message)
    assert len(result) == 1
    assert "doc_type" not in result[0]


def test_the_body_has_its_own_dedupe_claim(monkeypatch):
    """Keyed on `{message_id}:body`, so a redelivery re-publishes neither the
    attachment nor the body. A body that slipped through would be re-read and
    re-asserted as if the patient had written twice."""
    seen: list[str] = []

    def _claim(namespace, key):
        seen.append(key)
        return key not in seen[:-1]

    message = _message(parts=[_text_part(BODY), _pdf_part()])
    _run(monkeypatch, message, claims=_claim)
    assert f"msg_1:{pipeline.BODY_PART_ID}" in seen


def test_an_already_claimed_body_is_not_republished(monkeypatch):
    message = _message(parts=[_text_part(BODY), _pdf_part()])
    result, published, _ = _run(
        monkeypatch, message, claims=lambda ns, key: not key.endswith(":body")
    )
    assert len(result) == 1
    assert len(published) == 1


def test_a_failed_body_publish_releases_its_claim_and_reraises(monkeypatch):
    """Same provisional-claim contract as an attachment: a failure between
    the claim and the publish must leave the redelivery able to retry, not be
    waved through as a duplicate."""
    released: list[tuple] = []
    message = _message(parts=[_text_part(BODY), _pdf_part()])

    monkeypatch.setattr(pipeline.dedupe, "release", lambda ns, key: released.append((ns, key)))
    monkeypatch.setattr(pipeline.gmail_client, "fetch_message", lambda mid: message)
    monkeypatch.setattr(
        pipeline.gmail_client, "fetch_attachment_bytes", lambda mid, aid: b"%PDF fake"
    )
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: True)
    monkeypatch.setattr(
        pipeline.storage,
        "upload_attachment",
        lambda mid, part_id, filename, content, ct: f"gs://bucket/{part_id}",
    )

    calls = {"n": 0}

    def _flaky(topic, data):
        calls["n"] += 1
        if data.get("doc_type") == pipeline.PATIENT_STATEMENT_TYPE:
            raise RuntimeError("Deadline Exceeded")

    monkeypatch.setattr(pipeline.pubsub, "publish", _flaky)

    try:
        pipeline.process_new_message("msg_1")
    except RuntimeError:
        pass
    else:  # pragma: no cover -- the publish is supposed to raise
        raise AssertionError("the failed publish was swallowed")

    assert released == [("gmail_attachment", f"msg_1:{pipeline.BODY_PART_ID}")]
