"""One shared way to authenticate as the demo Gmail account.

Gmail, Calendar, and Drive (WO1, WO5, WO6) all act as the SAME demo Google
account, which needs a real user identity (a service account cannot watch a
personal Gmail inbox or own Calendar/Drive events without domain-wide
delegation, which this hackathon's demo account does not have). ATLAS's
`infra/OAUTH.md` (persona 1 WO3) owns getting the consent screen + refresh
token; this module owns turning that refresh token into `Credentials` objects
for every Google API client in the codebase, once, so the flow is not
reimplemented per caller.

`services/intake` imports this directly (it is on `pythonpath` alongside
`packages/delivery` per `pyproject.toml`) rather than duplicating it --
Gmail watch renewal and Calendar/Drive sync are the same "authenticate as
the demo account" problem.

Secrets (agreement §2.4): the refresh token and client secret are read from
env vars that Cloud Run populates from Secret Manager -- never hardcoded,
never committed. See the PR HANDOFF for the exact secret names, since
`.env.example` is FORGE's file.
"""

from __future__ import annotations

import os


class MissingCredentialsError(Exception):
    """Raised when the demo account's OAuth env vars are not configured.

    Callers should treat this as "the Google API integration is not wired up
    in this environment" and degrade gracefully (e.g. `calendar_sync` and
    `drive_sync` both return an empty result with a clear reason rather than
    raising past this point) -- a missing calendar sync must never take down
    a filing that already succeeded.
    """


def load_user_credentials(scopes: list[str]):
    """Build `google.oauth2.credentials.Credentials` for the demo account.

    Reads GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and
    GOOGLE_OAUTH_REFRESH_TOKEN from the environment. Import of
    `google.oauth2.credentials` is deferred so that modules importing this
    file do not require `google-auth` to be installed just to be imported
    (CI's base install is ruff+pytest only -- see the PR HANDOFF).
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise MissingCredentialsError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / "
            "GOOGLE_OAUTH_REFRESH_TOKEN not set -- see infra/OAUTH.md for how to mint "
            "the demo account's refresh token."
        )
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )
