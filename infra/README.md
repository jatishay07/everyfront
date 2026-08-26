# `infra/`

**Owner:** ATLAS (persona 1)

setup.sh, deploy.sh, service configs, OAUTH.md. Shell + gcloud only -- no Terraform, no GKE (persona guardrail).

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[ATLAS] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.

---

## Security posture (read this before you demo, and before you put real data anywhere near this)

**There is no authentication on anything.** This is a stated choice, verified
live against the deployed project (2026-08-25/26), not an oversight -- but it
needs to be a choice everyone downstream is actually making with eyes open:

- `deploy.sh` deploys every service (`ef-web`, `ef-api`, `ef-agent-core`,
  `ef-intake`) with `--allow-unauthenticated`. Confirmed live: all four
  Cloud Run services grant `roles/run.invoker` to `allUsers`
  (`gcloud run services get-iam-policy <svc>`). Anyone who has, or guesses,
  a `*.run.app` URL can call it -- no API key, no OAuth, no IP allowlist.
- `services/api` has no auth layer of its own either (no API-key dependency,
  no bearer-token check, no session) -- confirmed by reading `main.py`. That
  means `GET /cases`, `GET /cases/{id}`, and **`POST
  /cases/{id}/approve_filing`** (the human-in-the-loop filing gate) are all
  reachable by anyone with the URL. There is no "human" the gate actually
  authenticates -- it is a UI affordance, not an access control.
- `services/api` sends no CORS headers (an OPTIONS preflight to `/cases`
  returns a bare 405), so `web/`'s server-side proxy
  (`web/app/api/proxy/[...path]/route.ts`) is the only thing standing between
  a browser and the raw API -- and that proxy adds no auth of its own either.
- Firestore is accessed only through each service's Admin-SDK service
  account (never a client-side Firestore SDK), so Firestore security rules
  are not in play -- the entire access-control surface for `cases/`,
  `filings/`, and `hospitals/` is "does this Cloud Run service accept your
  request," which per the above is "yes, unconditionally."

**Why this is a defensible choice for this project, right now:** every
record behind these URLs is synthetic fixture data (BUILD_PLAYBOOK.md §2.6,
CLAUDE.md) -- there is no real patient, no real SSN, no real bill anywhere in
this system. Cutting auth to zero is what made the "one command, no console
clicks, 30-minute fresh deploy" acceptance bar (§4 persona 1) achievable
inside the hackathon's timeline, and a judge hitting the live URL directly is
a feature of the submission, not a bug.

**Why it stops being defensible the moment real data shows up:** none of
this is what you'd want if `cases/` ever held a real patient's income,
household, or billing history. There is no audit trail of *who* approved a
filing (`events/` records which *agent* acted, never which *human* clicked
approve), and no way to scope one advocate's cases away from another's.
Before this handles anything real, at minimum: put `ef-api` and `ef-web`
behind Identity-Aware Proxy or a real auth layer (Firebase Auth / OAuth is
already half-wired for the demo Gmail account -- see below), stop granting
`allUsers` `run.invoker`, and add per-request identity to the `events/`
audit trail.

**Related, separately-tracked gap found during this same live audit:**
`infra/README.md` (this file, line 5) has referenced an `OAUTH.md` since
persona 1's original work order, but it does not exist yet, and none of
`GOOGLE_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` exist in
Secret Manager (confirmed live: `gcloud secrets list` is empty). Confirmed
live by actually calling `POST /gmail/watch/renew` on the deployed
`ef-intake`: it 500s with `MissingCredentialsError: ... -- see
infra/OAUTH.md for how to mint the demo account's refresh token`
(`services/intake/intake/google_auth.py`). The Pub/Sub wiring, the Cloud
Scheduler renewal job, and the push endpoint itself are all live and correct
(see below) -- but the Gmail watch they exist to keep alive cannot start at
all without those three secrets, which requires an interactive OAuth
consent flow no script in this repo can perform unattended. HANDOFF: this
needs a human to run the consent flow for the demo Gmail account and land
the resulting values in Secret Manager (`ef-intake`'s service account
already has `roles/secretmanager.secretAccessor`, provisioned and ready);
until then, the demo's Gmail intake path only works via
`POST /demo/inject_bill`, which bypasses Gmail entirely by design.
