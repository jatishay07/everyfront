#!/usr/bin/env python3
"""One-time, human-run script: mint the demo Gmail account's OAuth refresh
token that `packages/delivery/delivery/google_auth.py` and
`services/intake/intake/google_auth.py` read at runtime.

WHY THIS HAS TO BE RUN BY A HUMAN, ONCE, ON A LAPTOP
-----------------------------------------------------
Gmail, Calendar, and Drive (WO1/WO5/WO6) all act as the demo GOOGLE ACCOUNT
-- not a service account, which cannot watch a personal Gmail inbox or own
Calendar/Drive events without domain-wide delegation this hackathon's demo
account does not have. Minting a refresh token for a real Google account
requires a real consent screen click by someone logged into that account's
browser session. No amount of code closes that gap -- it is the one
irreducibly-human step in this work order. See `services/intake/README.md`
for the exact numbered steps around running this (creating the OAuth client
in the Cloud Console, then this script, then storing the result).

WHAT THIS SCRIPT DOES
----------------------
Runs the standard installed-app OAuth flow (a local HTTP server on
localhost, no `--noauth_local_webserver` deprecated flag needed with modern
`google-auth-oauthlib`) for the UNION of every scope any module in this
codebase reads this refresh token to satisfy:

  - Gmail readonly   (services/intake/intake/gmail_client.py)
  - Calendar          (packages/delivery/delivery/calendar_sync.py)
  - Drive.file         (packages/delivery/delivery/drive_sync.py)

A refresh token is only valid for the scopes it was originally granted
under -- minting one for gmail.readonly alone and expecting it to also work
for Calendar/Drive would fail silently at first API call. This script asks
for the union ONCE so a single GOOGLE_OAUTH_REFRESH_TOKEN env var covers all
three call sites, matching how `google_auth.py` is actually invoked
throughout this codebase.

USAGE
-----
    pip install google-auth-oauthlib==1.4.1  # already in requirements.txt

    # Option A: you downloaded client_secret.json from the Cloud Console
    # (APIs & Services > Credentials > your Desktop app OAuth client > download JSON)
    python mint_oauth_token.py --client-secrets ~/Downloads/client_secret_xxx.json

    # Option B: you already have the client id/secret as strings
    python mint_oauth_token.py --client-id ...apps.googleusercontent.com --client-secret ...

A browser window opens; sign in as the DEMO Gmail account (not your own),
click Allow on every scope, and the script prints the refresh token plus the
exact `gcloud secrets` commands to store it -- it never writes the token to
disk itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
]


def _client_secrets_path(args: argparse.Namespace) -> str:
    if args.client_secrets:
        return args.client_secrets
    if not (args.client_id and args.client_secret):
        sys.exit(
            "error: pass either --client-secrets <path to the JSON the Cloud Console gave you> "
            "or both --client-id and --client-secret"
        )
    # InstalledAppFlow wants a client-secrets-shaped JSON file either way --
    # synthesize one from the two strings rather than requiring the human to
    # hand-author it. `installed` (not `web`): this app has no fixed
    # redirect URI, it's the desktop/local-server flow.
    payload = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(payload))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secrets", help="Path to the client_secret_*.json downloaded from Cloud Console"
    )
    parser.add_argument("--client-id", help="OAuth client id (alternative to --client-secrets)")
    parser.add_argument(
        "--client-secret", help="OAuth client secret (alternative to --client-secrets)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local server port for the redirect (0 = pick any free port)",
    )
    args = parser.parse_args()

    # Imported here, not at module scope: this script is never imported by
    # the service or by pytest, only run directly by a human, so there is no
    # test-collection reason to defer it -- but google-auth-oauthlib is not
    # installed in most environments that might accidentally `import` this
    # file (e.g. a stray test-discovery pass), and failing at run() rather
    # than at import is the more useful error there too.
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets_path = _client_secrets_path(args)
    flow = InstalledAppFlow.from_client_secrets_file(secrets_path, scopes=SCOPES)
    print(
        "Opening a browser for consent. Sign in as the DEMO Gmail account "
        "(not your personal account) and click Allow for every scope shown.\n",
        file=sys.stderr,
    )
    # `prompt="consent"` is load-bearing, not cosmetic. `access_type=offline`
    # (which google-auth-oauthlib already sets by default) only asks Google to
    # ISSUE a refresh token; Google returns one just once per (client_id,
    # account) grant and then silently omits it from every later exchange. So
    # the second run of this script -- a re-mint after a typo, a rotation, a
    # second demo account -- would complete the whole browser dance and then
    # die at the check below, sending a human off to
    # myaccount.google.com/permissions to revoke by hand mid-runbook.
    # `prompt="consent"` forces the consent screen every time and makes Google
    # re-issue the refresh token unconditionally, so this script is as
    # re-runnable as the rest of the go-live path (go_live.sh is already
    # idempotent by construction).
    creds = flow.run_local_server(port=args.port, prompt="consent")

    if not creds.refresh_token:
        sys.exit(
            "error: Google did not return a refresh_token even though this script "
            "requested prompt=consent. Revoke this client's grant at "
            "https://myaccount.google.com/permissions and re-run; if it still fails, "
            "the OAuth client is probably a 'Web application' type rather than the "
            "'Desktop app' type infra/OAUTH.md step 3 calls for."
        )

    print("\n" + "=" * 78)
    print("SUCCESS -- do not commit any of this. Store it in Secret Manager, not in code.")
    print("=" * 78)
    print(f"\nGOOGLE_OAUTH_CLIENT_ID     = {creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET = {creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN = {creds.refresh_token}")
    print(
        "\nNext: run services/intake/scripts/go_live.sh with these three values "
        "as env vars -- it creates the Secret Manager secrets, wires them into "
        "the deployed Cloud Run services, converts the Gmail push subscription, "
        "and schedules the 7-day watch renewal. See services/intake/README.md "
        "for the exact numbered steps."
    )


if __name__ == "__main__":
    main()
