# `services/intake/`

**Owner:** RELAY (persona 4)

Gmail push webhook -> Pub/Sub -> GCS. Publishes case.document.added.

---

## Making Gmail intake live (WO7)

Everything below EXCEPT step 1 is scriptable and this repo does it for you.
Step 1 cannot be: minting an OAuth refresh token for a real Google account
requires a human, logged into that account's browser, clicking "Allow" on a
real Google consent screen. No amount of code closes that gap.

**What "live" means concretely:** today, `POST /demo/inject_bill` is the
only way a bill enters this system. After these steps, emailing a PDF to the
demo Gmail account does the same thing for real -- Gmail pushes a
notification through Pub/Sub, this service downloads the message, extracts
the PDF's text, uploads it to GCS, and publishes `case.document.added`
exactly as `/demo/inject_bill` does today.

**What still needs a SWARM-side fix before an emailed bill produces a full
case (see this work order's PR description for the exact patch):**
`case.document.added` for a Gmail-sourced document names a `case_id` /
`doc_id` that nothing has created in Firestore yet (`services/intake` has no
Firestore grant -- see `dedupe.py`). `agent-core`'s document-added handler
needs to auto-create both records from the event's own `gcs_uri` / `filename`
/ `raw_text` fields (all present as of this work order) before running
Reader. Without that one small addition on the agent-core side, a real
emailed bill will land in GCS and publish its event correctly, but the
pipeline will not pick it up.

### Steps

1. **(Human, one time, ~5 minutes) Create the OAuth client and mint the
   refresh token.**
   1. In the Cloud Console for this project: **APIs & Services > Credentials
      > Create Credentials > OAuth client ID**. Application type: **Desktop
      app**. Name it anything (e.g. "Every Front demo intake"). Download the
      resulting JSON.
      - If this project's OAuth consent screen doesn't exist yet: **APIs &
        Services > OAuth consent screen**, User type **External**, fill in
        the app name/support email, add the demo Gmail account as a **test
        user** (this keeps the app in "Testing" status, which is fine --
        it's never submitted for verification), and add these three scopes:
        `gmail.readonly`, `calendar`, `drive.file`.
   2. On your own machine, with this repo checked out:
      ```
      cd services/intake
      pip install google-auth-oauthlib==1.4.1  # or: pip install -r requirements.txt
      python scripts/mint_oauth_token.py --client-secrets ~/Downloads/client_secret_*.json
      ```
   3. A browser window opens. **Sign in as the demo Gmail account** (not your
      own) and click **Allow** for every scope shown.
   4. The script prints three values: `GOOGLE_OAUTH_CLIENT_ID`,
      `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`. Copy them --
      they are never written to disk by the script.

2. **(Scriptable) Provision secrets, wire them into the deployed services,
   flip the Gmail push subscription on, and schedule watch renewal.** Run
   after `infra/deploy.sh all` has deployed `ef-intake` and `ef-agent-core`
   at least once:
   ```
   PROJECT_ID=<your project> \
   GOOGLE_OAUTH_CLIENT_ID=<from step 1> \
   GOOGLE_OAUTH_CLIENT_SECRET=<from step 1> \
   GOOGLE_OAUTH_REFRESH_TOKEN=<from step 1> \
   ./services/intake/scripts/go_live.sh
   ```
   This is idempotent -- safe to re-run after rotating a key or redeploying.
   It tells you exactly what it wired and what it skipped. See the script's
   own header comment for the full list of optional vars (Phaxio/Lob keys,
   fax/mail allowlists, Calendar id, Drive folder/advocate email) and exactly
   what stays off without each one.

3. **(Human, optional but recommended) Verify.** Send a real email with a
   PDF attachment to the demo Gmail account, then:
   ```
   gcloud run services logs read ef-intake --region=$REGION --limit=50
   gcloud storage ls gs://ef-documents-$PROJECT_ID/intake/
   ```
   You should see the message processed and the attachment land in GCS
   within a minute or two of Gmail's push notification arriving.

### What's real vs. what's the fallback today

- **Real, live-tested:** the PDF form-fill engine (5 forms, 3 of them real
  government/hospital PDFs), the fax/mail vendor interface + in-code
  destination allowlist, the Gmail webhook -> GCS -> Pub/Sub pipeline (unit
  tested against faked Gmail/GCS/Pub/Sub; the Docker image itself boots and
  answers `/health`).
- **Real once a human completes step 1 above:** the OAuth-gated calls
  themselves (`users.watch`, `messages.get`, Calendar/Drive writes) -- the
  code path is identical whether credentials are configured or not; only the
  credentials are missing without a human's consent click.
- **Honest fallback, clearly labelled, never silent:** without Phaxio/Lob
  credentials, `packages/delivery/delivery/vendors/fake.py`'s
  `FakeFaxVendor`/`FakeMailVendor` record the send and return a realistic
  vendor id + tracking number; every such result carries `"vendor": "fake"`
  and `"simulated": true` in the case's audit trail -- it is never reported
  as a real Phaxio/Lob send. `/demo/inject_bill` remains the documented,
  sanctioned fallback path for the demo itself (§6 risk register) if Gmail
  cannot be shown live on camera.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[RELAY] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
