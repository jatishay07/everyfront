"""OAuth credentials for the demo Gmail account.

Deliberately a standalone copy of `packages/delivery/delivery/google_auth.py`
rather than a cross-package import: `infra/deploy.sh` builds each Cloud Run
service with `--source=services/<name>`, which scopes the Cloud Build
context to that directory alone -- a `from delivery... import` here would
build locally (pyproject's `pythonpath` puts both on `sys.path` for pytest)
but FAIL IN PRODUCTION, since `packages/delivery` would not exist inside this
service's build context. See the PR's HANDOFF: this is a real gap in
`deploy.sh` that also affects `services/agent-core`'s stated dependency on
`packages/rules` and `packages/delivery` (§4 persona 5 "Depends on") -- it is
flagged for FORGE/ATLAS rather than silently worked around at the
architecture level, since `infra/` is outside RELAY's owned paths. Within
this service, the fix is simply: do not create the cross-directory import in
the first place.
"""

from __future__ import annotations

import os


class MissingCredentialsError(Exception):
    """Demo account OAuth env vars are not configured -- callers should
    degrade gracefully rather than raise past this point."""


def load_user_credentials(scopes: list[str]):
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
