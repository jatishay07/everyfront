# `infra/OAUTH.md` — minting the demo Google account's OAuth token

**Owner:** ATLAS (persona 1), content contributed by RELAY (persona 4, WO8) —
this file has been referenced by `infra/README.md`, and by both
`packages/delivery/delivery/google_auth.py` and
`services/intake/intake/google_auth.py`'s `MissingCredentialsError` messages,
since persona 1's original work order. It did not exist. This closes that
gap.

## Why this step exists and can't be scripted away

Gmail intake (`services/intake`), the demo Calendar sync, and the per-case
Drive mirror (both `packages/delivery`) all act as **one real Google
account** — the demo Gmail inbox — not a service account. A service account
cannot watch a personal Gmail inbox or own Calendar events/Drive files
without domain-wide delegation, which this hackathon's demo account does not
have. Minting an OAuth **refresh token** for a real Google account requires a
human, logged into that account's own browser session, clicking "Allow" on a
real Google consent screen once. No script in this repo can perform that
click unattended — everything else below *is* scripted.

**Status as of this work order (2026-08-26):** not yet done.
`gcloud secrets list` has no `google-oauth-*` entries, and
`POST /gmail/watch/renew` on the deployed `ef-intake` 500s with
`MissingCredentialsError` pointing back at this file (confirmed live by
ATLAS's infra audit, see `infra/README.md`'s security-posture section). Until
someone completes the steps below, Gmail intake only works via
`POST /demo/inject_bill` (the documented, sanctioned fallback — §6 risk
register), and both Calendar sync and Drive mirroring no-op cleanly (return
`[]` / `None`, never raise — see `google_auth.MissingCredentialsError`'s own
docstring) everywhere they're called on the live path.

## What you end up with

One refresh token, good for three scopes at once (Gmail read, Calendar,
Drive), stored as three Secret Manager secrets and wired as env vars into
`ef-intake` and `ef-agent-core`:

| Env var | Used by | For |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | both services | OAuth client identity |
| `GOOGLE_OAUTH_CLIENT_SECRET` | both services | OAuth client identity |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | both services | the actual credential |

Scopes granted, in one consent (see `services/intake/scripts/mint_oauth_token.py`'s
`SCOPES` — this is the single source of truth; keep this table in sync with it):

- `https://www.googleapis.com/auth/gmail.readonly` — `services/intake` reads
  the demo inbox and its attachments (never sends or deletes mail).
- `https://www.googleapis.com/auth/calendar` — `packages/delivery/delivery/calendar_sync.py`
  writes one event per statutory deadline to the demo Calendar.
- `https://www.googleapis.com/auth/drive.file` — `packages/delivery/delivery/drive_sync.py`
  mirrors each case's generated filings to a per-case Drive folder. This
  scope (not the broader `drive`) means the app can only see/edit files it
  itself created — it can never read the demo account's other Drive content.

## Steps (≈5 minutes once you're signed in as the demo account)

1. **Sign in to the Cloud Console as a user with access to this GCP project**,
   then switch your Google session to the **demo Gmail account** in a
   separate browser profile/incognito window — you will need to be signed in
   AS the demo account (not your own) partway through step 4, and Google's
   consent screen is account-specific.

2. **Create the OAuth consent screen** (skip if it already exists for this
   project): Cloud Console → **APIs & Services → OAuth consent screen**.
   - User type: **External**.
   - App name / support email: anything (e.g. "Every Front demo").
   - **Test users**: add the demo Gmail account's own address. This keeps the
     app in "Testing" status, which is fine — it is never submitted for
     verification, and Testing-status apps are exempt from Google's
     verification review as long as every user is listed as a test user.
   - Scopes screen: add the three scopes listed in the table above
     (`gmail.readonly`, `calendar`, `drive.file`) — or skip adding them here
     and just accept whatever `mint_oauth_token.py` requests in step 4; both
     work, the script is the actual source of truth.

3. **Create an OAuth client**: **APIs & Services → Credentials → Create
   Credentials → OAuth client ID**. Application type: **Desktop app**
   (not "Web application" — there is no fixed redirect URI here; the script
   runs a local server). Name it anything. **Download the JSON.**

4. **Run the minting script**, signed into the browser session as the
   **demo Gmail account**:
   ```bash
   cd services/intake
   pip install google-auth-oauthlib==1.4.1   # or: pip install -r requirements.txt
   python scripts/mint_oauth_token.py --client-secrets ~/Downloads/client_secret_*.json
   ```
   A browser window opens. Sign in as the **demo account** and click
   **Allow** for all three scopes. The script prints three values —
   `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
   `GOOGLE_OAUTH_REFRESH_TOKEN` — to your terminal only. It never writes them
   to disk.
   - If it exits with *"Google did not return a refresh_token"*: this
     client_id already has a live grant for this account. Revoke it at
     <https://myaccount.google.com/permissions> and re-run.

5. **Land the token: create the Secret Manager secrets, wire them into the
   deployed services, flip Gmail's push subscription on, and schedule watch
   renewal — one command** (this is scriptable, and already written):
   ```bash
   PROJECT_ID=<your project> \
   GOOGLE_OAUTH_CLIENT_ID=<from step 4> \
   GOOGLE_OAUTH_CLIENT_SECRET=<from step 4> \
   GOOGLE_OAUTH_REFRESH_TOKEN=<from step 4> \
   ./services/intake/scripts/go_live.sh
   ```
   Run this after `infra/deploy.sh all` has deployed `ef-intake` and
   `ef-agent-core` at least once. It is idempotent — safe to re-run after
   rotating a key or redeploying — and tells you exactly what it wired and
   what it skipped. Optional at the same time, same command (see the
   script's own header for the full list): `PHAXIO_API_KEY`/
   `PHAXIO_API_SECRET`, `LOB_API_KEY`, `GOOGLE_CALENDAR_ID` (defaults to
   `primary` — the demo account's own default calendar — if unset),
   `GOOGLE_DRIVE_ROOT_FOLDER_ID` (root of My Drive if unset),
   `GOOGLE_DRIVE_ADVOCATE_EMAIL` (skips sharing the per-case folder if unset).

6. **Verify all three integrations, not just Gmail:**
   ```bash
   # Gmail: send a real email with a PDF attachment to the demo inbox, then
   gcloud run services logs read ef-intake --region=$REGION --limit=50
   gcloud storage ls gs://ef-documents-$PROJECT_ID/intake/
   # -> the message should be processed and the PDF should land in GCS
   #    within a minute or two of Gmail's push notification.

   curl -X POST "$(gcloud run services describe ef-intake --region=$REGION \
     --format='value(status.url)')/gmail/watch/renew"
   # -> 200 with a historyId, not a 500 MissingCredentialsError.

   # Calendar + Drive: inject a fixture case and approve a filing with a
   # real deadline (e.g. ef-2026-0001, PPDR front — 120-day deadline), then
   # check the demo account's own Calendar (event titled "[PPDR] ..." with
   # the citation in the description, colored red if due within 7 days) and
   # Drive (a folder named "case-ef-2026-0001" containing the filed PDF).
   ```

## What's real vs. what's still the fallback

- **Real, live-tested independent of this step:** Gmail webhook → GCS →
  Pub/Sub plumbing, Calendar/Drive sync's pure logic (stable event ids,
  red-if-due-soon coloring, the credentials-missing → clean no-op path) —
  all unit-tested against faked Google API clients.
- **Real once steps 1–5 above are done by a human:** the actual OAuth-gated
  API calls (`users.watch`, `messages.get`, Calendar event upsert, Drive file
  upload) — the code path is byte-for-byte identical whether credentials are
  configured or not; only the credentials are missing without this step.
- **Honest fallback, clearly labelled, never silent:** without these
  secrets, `sync_deadlines`/`mirror_case_filings` return `[]`/`None` and log
  nothing false — no filing, no case analysis, and no Gmail push ever fails
  or blocks *because* Calendar/Drive aren't configured (see
  `google_auth.MissingCredentialsError`'s docstring: "a missing calendar
  sync must never fail the filing that triggered it").

---

Rules of engagement (BUILD_PLAYBOOK.md §0) apply to this file the same as any
other `infra/` content: cross-agent communication goes through the contracts
in §3, and a `HANDOFF:` note belongs in the PR description for anything this
persona can't finish alone.
