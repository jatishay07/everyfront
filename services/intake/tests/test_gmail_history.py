"""`gmail_client.list_new_message_ids` -- the INBOX filter and the
expired-cursor 404.

Separate from `test_gmail_client.py` (which covers the pure MIME-walking
helpers) because everything here needs a faked Gmail service object.

Nothing here imports `googleapiclient`. It is a real dependency of this
service (`requirements.txt`) and CI installs it, but it is NOT in the repo's
local `.venv` -- a module-level import would pass CI and fail on the
maintainer's machine at collection time. The 404 is therefore faked by
shape, which is also what `_is_history_expired` actually reads.
"""

from __future__ import annotations

import pytest
from intake import gmail_client


class _FakeHttpError(Exception):
    """Shaped like `googleapiclient.errors.HttpError`.

    The shape was read out of the pinned google-api-python-client 2.199.0
    wheel's `googleapiclient/errors.py`, not assumed: `HttpError.__init__`
    assigns `self.resp`, and `status_code` is a `@property` returning
    `self.resp.status`. Both are set here so the test covers both branches of
    `_is_history_expired`.
    """

    def __init__(self, status: int):
        super().__init__(f"<HttpError {status}>")
        self.status_code = status
        self.resp = type("_Resp", (), {"status": status})()


def _fake_service(*, pages, error=None, recorder=None):
    class _FakeHistory:
        def list(self, **kwargs):
            if recorder is not None:
                recorder.append(kwargs)
            return self

        def execute(self):
            if error is not None:
                raise error
            return pages.pop(0)

    class _FakeUsers:
        def history(self):
            return _FakeHistory()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    return _FakeService()


def test_history_list_is_filtered_to_the_inbox(monkeypatch):
    """REGRESSION: `users.watch` filters which changes TRIGGER a
    push notification, but `history.list` does not inherit that filter. Called
    unfiltered it reports `messageAdded` for EVERY label -- so a draft, a sent
    message, or a spam message that happens to carry a PDF gets ingested and
    published as `case.document.added` exactly as if a patient had emailed a
    bill in. On a live demo account that is anything the operator touches.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        gmail_client,
        "_service",
        lambda: _fake_service(pages=[{"history": []}], recorder=calls),
    )

    gmail_client.list_new_message_ids("100")

    assert calls, "history().list() was never called"
    assert calls[0].get("labelId") == "INBOX", (
        "history.list must be filtered to INBOX or intake ingests drafts, sent mail "
        f"and spam as if they were emailed bills; got {calls[0]!r}"
    )


def test_expired_start_history_id_raises_history_expired(monkeypatch):
    """REGRESSION: Gmail keeps its history log for a limited
    window and answers 404 for any `startHistoryId` older than that. Untyped,
    that 404 escapes as a raw HttpError, 500s the route, and -- because the
    Pub/Sub claim was already taken -- gets swallowed as a "duplicate" on
    redelivery. The cursor never advances, so the NEXT push 404s too. Intake
    goes dark after any gap longer than the history window while every
    response says 200.
    """
    monkeypatch.setattr(
        gmail_client,
        "_service",
        lambda: _fake_service(pages=[], error=_FakeHttpError(404)),
    )

    with pytest.raises(gmail_client.HistoryExpired) as exc:
        gmail_client.list_new_message_ids("100")
    assert "100" in str(exc.value)


def test_a_non_404_error_is_not_mistaken_for_an_expired_cursor(monkeypatch):
    """A 500 is transient and MUST keep propagating: re-bootstrapping the
    watch on a transient blip would throw away a perfectly good cursor and
    skip every message that was still recoverable from it.
    """
    monkeypatch.setattr(
        gmail_client,
        "_service",
        lambda: _fake_service(pages=[], error=_FakeHttpError(500)),
    )

    with pytest.raises(_FakeHttpError):
        gmail_client.list_new_message_ids("100")


def test_404_is_detected_from_resp_status_alone(monkeypatch):
    """The `err.resp.status` fallback branch of `_is_history_expired`.

    On the real `HttpError` the two attributes cannot disagree (`status_code`
    is a property over `resp.status`), so this covers the case the fallback
    actually exists for: something HttpError-SHAPED that only carries `resp`.
    """

    class _RespOnly(Exception):
        def __init__(self, status: int):
            super().__init__(f"<HttpError {status}>")
            self.resp = type("_Resp", (), {"status": status})()

    monkeypatch.setattr(
        gmail_client, "_service", lambda: _fake_service(pages=[], error=_RespOnly(404))
    )
    with pytest.raises(gmail_client.HistoryExpired):
        gmail_client.list_new_message_ids("100")


def test_an_error_carrying_no_status_at_all_propagates(monkeypatch):
    """A socket timeout has neither attribute. It must not be silently read as
    an expired cursor -- that would burn the watch and skip live mail on any
    network blip."""
    monkeypatch.setattr(
        gmail_client, "_service", lambda: _fake_service(pages=[], error=OSError("connection reset"))
    )
    with pytest.raises(OSError, match="connection reset"):
        gmail_client.list_new_message_ids("100")


def test_paginates_across_history_pages_and_filters_every_page(monkeypatch):
    """Pre-existing pagination behaviour, pinned while this function is being
    edited -- plus: the INBOX filter has to be on page 2 as well, or the
    filter silently stops applying past the first 100 history records."""
    pages = [
        {
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
            "nextPageToken": "tok",
        },
        {"history": [{"messagesAdded": [{"message": {"id": "m2"}}]}]},
    ]
    calls: list[dict] = []
    monkeypatch.setattr(
        gmail_client, "_service", lambda: _fake_service(pages=pages, recorder=calls)
    )

    assert gmail_client.list_new_message_ids("100") == ["m1", "m2"]
    assert [c.get("labelId") for c in calls] == ["INBOX", "INBOX"]
    assert [c.get("pageToken") for c in calls] == [None, "tok"]
