#!/usr/bin/env bash
# RELAY (persona 4) -- ONE COMMAND THAT SAYS WHICH STAGE OF GMAIL INTAKE IS BROKEN.
#
# WHAT THIS IS FOR
# ----------------
# `go_live.sh` PROVISIONS. This VERIFIES. Run it immediately after `go_live.sh`,
# the moment a human has finished the Google consent screen (infra/OAUTH.md
# step 4). Not one line of the OAuth-gated path -- `_service()`, `users.watch`,
# `history.list`, `messages.get`, `messages.attachments.get` -- has ever
# executed against Google. `tests/test_gmail_transport.py` shrinks that risk as
# far as a fake can: it proves this service handles the shapes Gmail's own
# discovery document describes. It cannot prove Gmail sends those shapes, that
# the token works, or that the IAM around the topic is real. This script is
# where those three questions get answered, and it is written so that the
# answer is a named stage and a real error body rather than two hours of blind
# debugging.
#
# WHY BASH, NOT PYTHON
# --------------------
# 1. Every step is a `gcloud` or `curl` invocation whose RAW RESPONSE BODY is
#    the diagnostic. In bash that body is already a string on its way to the
#    screen; in Python it is a thing to be deserialised, re-serialised and
#    accidentally truncated. This project's defect #2 was a failure whose cause
#    was only stated in a response body that the code threw away.
# 2. It has to run on the operator's laptop in the two minutes after the
#    consent screen, where the only guaranteed toolchain is `gcloud` and
#    `curl`. `mint_oauth_token.py` tells the operator to `pip install
#    google-auth-oauthlib` FIRST, i.e. a working Python environment is
#    something this repo asks the operator to build, not something it may
#    assume already exists.
# 3. It sits beside `go_live.sh` and is run back-to-back with it. Identical
#    `log`/`ok`/`skip`/`warn`/`die` vocabulary, identical colours, identical
#    exit convention -- one transcript, one mental model.
# No step here holds a data structure that outlives it, which is the only thing
# that would have argued for Python.
#
# SAFETY
# ------
# Idempotent and safe to re-run as often as you like. It MUTATES EXACTLY TWO
# THINGS, both deliberately and both idempotent:
#   * it starts (re-arms) the Gmail watch -- that is stage 3, and it is the
#     only way to find out whether Gmail accepts the watch at all;
#   * it publishes ONE synthetic notification to `intake.email.received` in
#     stage 5, carrying the historyId stage 3 just returned. Because that is
#     also the cursor stage 3 just stored, `history.list` diffs it against
#     itself and finds nothing: no message is fetched, nothing is written to
#     GCS, nothing is published downstream. (If you sent a real bill between
#     stage 3 and stage 5, that message WILL be ingested here -- which is the
#     behaviour you wanted anyway.) It leaves behind one dedupe marker blob
#     under `_dedupe/gmail_push/`, which is inert.
# It creates no secrets, deploys nothing, and changes no IAM, no subscription
# and no Cloud Run configuration. Everything else it does is a read.
#
# EXIT CODE
# ---------
# Non-zero on the first GENUINE failure, with the actual error body printed --
# never a bare exception, never a stack trace, never "something went wrong".
# "Genuine" is doing real work in that sentence: stages 6 and 7 check for
# artefacts that only exist once a human has actually emailed a bill in, so by
# default they report PENDING and tell you what to do next. Pass --after-email
# (or set AFTER_EMAIL=1) on the re-run to turn them into hard failures.
#
# USAGE
# -----
#   PROJECT_ID=everyfront-hack-2026 ./services/intake/scripts/verify_live.sh
#   # ... send a real email with a PDF attachment to the demo inbox ...
#   PROJECT_ID=everyfront-hack-2026 ./services/intake/scripts/verify_live.sh --after-email
#
# Credentials are read from Secret Manager (whatever `go_live.sh` landed there)
# unless GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN are already in the
# environment. Reading them from Secret Manager is the point: it verifies the
# values the DEPLOYED SERVICE will use, not the ones in your shell.
set -euo pipefail

# Arguments first, so `--help` works without a project selected.
AFTER_EMAIL="${AFTER_EMAIL:-0}"
for arg in "$@"; do
  case "$arg" in
    --after-email) AFTER_EMAIL=1 ;;
    -h|--help) sed -n '2,95p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s (try --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID -- e.g. PROJECT_ID=everyfront-hack-2026 $0}"
REGION="${REGION:-us-central1}"
TOPIC="${TOPIC_INTAKE_EMAIL_RECEIVED:-intake.email.received}"
SUBSCRIPTION="${SUBSCRIPTION_INTAKE_EMAIL:-ef-intake-email}"
DOCS_BUCKET="${GCS_DOCUMENTS_BUCKET:-ef-documents-${PROJECT_ID}}"
GMAIL_PUSH_SA="gmail-api-push@system.gserviceaccount.com"
PUSH_WAIT_SECONDS="${PUSH_WAIT_SECONDS:-60}"

log()     { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()      { printf '    \033[32mPASS\033[0m %s\n' "$*"; }
skip()    { printf '    \033[2m--  \033[0m %s\n' "$*"; }
warn()    { printf '    \033[33mwarn\033[0m %s\n' "$*"; }
pending() { printf '    \033[33mPEND\033[0m %s\n' "$*"; }
die()     { printf '\n\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# Indent a captured response body under a failure message so it is obviously
# Google's words and not ours. Never used on anything that could contain a
# token -- see stage 1.
body() { printf '%s\n' "${1:-(empty)}" | sed 's/^/    /'; }

# A failure that is only a failure once a bill has actually been emailed in.
soft_or_die() {
  if [ "$AFTER_EMAIL" = "1" ]; then die "$@"; fi
  pending "$1"
  printf '    \033[2m(not a failure yet -- re-run with --after-email once you have emailed a bill in)\033[0m\n'
}

command -v gcloud >/dev/null || die "gcloud is not installed. https://cloud.google.com/sdk/docs/install"
command -v curl   >/dev/null || die "curl is not installed."
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1 ||
  die "could not select project ${PROJECT_ID}. Run: gcloud auth login"

printf '\033[1mEvery Front -- Gmail intake live verification\033[0m\n'
printf 'project=%s region=%s topic=%s\n' "$PROJECT_ID" "$REGION" "$TOPIC"
[ "$AFTER_EMAIL" = "1" ] &&
  printf 'mode: --after-email (stages 6 and 7 are hard failures)\n' ||
  printf 'mode: pre-email (stages 6 and 7 report PENDING)\n'

# ===========================================================================
log "STAGE 0 -- the intake service is deployed and configured"
# ===========================================================================
# Cheap, and it removes the two failure modes that look identical to "Gmail is
# broken" from the operator's chair: no service at all, and a service missing
# the one env var every attachment upload does a hard `os.environ[...]` on.
INTAKE_URL="$(gcloud run services describe ef-intake --region="$REGION" \
  --format='value(status.url)' 2>/dev/null || true)"
[ -n "$INTAKE_URL" ] || die "ef-intake is not deployed in ${REGION}, so there is nothing to verify.
  Run:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh intake
        PROJECT_ID=${PROJECT_ID} ... ./services/intake/scripts/go_live.sh
  then re-run this script."
ok "ef-intake -> ${INTAKE_URL}"

_health_code="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "${INTAKE_URL}/health" || true)"
[ "$_health_code" = "200" ] ||
  die "GET ${INTAKE_URL}/health returned ${_health_code:-no response}, not 200.
  The container is not serving. Read the last deploy's logs:
    gcloud run services logs read ef-intake --region=${REGION} --limit=50"
ok "GET /health -> 200"

INTAKE_ENV="$(gcloud run services describe ef-intake --region="$REGION" \
  --format='value(spec.template.spec.containers[0].env)' 2>/dev/null || true)"
case "$INTAKE_ENV" in
  *GCS_DOCUMENTS_BUCKET*) ok "GCS_DOCUMENTS_BUCKET is set on the running revision" ;;
  *) die "ef-intake has no GCS_DOCUMENTS_BUCKET env var.
  services/intake/intake/storage.py does a hard os.environ[\"GCS_DOCUMENTS_BUCKET\"]
  (no default, deliberately), so EVERY attachment upload would KeyError -- after
  Gmail, Pub/Sub and the push endpoint had all worked perfectly. go_live.sh sets
  it; re-run it:
    PROJECT_ID=${PROJECT_ID} GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \\
    GOOGLE_OAUTH_REFRESH_TOKEN=... ./services/intake/scripts/go_live.sh" ;;
esac
case "$INTAKE_ENV" in
  *GOOGLE_OAUTH_REFRESH_TOKEN*) ok "GOOGLE_OAUTH_* secrets are mounted on the revision" ;;
  *) die "ef-intake has no GOOGLE_OAUTH_REFRESH_TOKEN. Every Gmail call will raise
  MissingCredentialsError (see intake/google_auth.py). Mint the token
  (infra/OAUTH.md) and run go_live.sh, which wires it." ;;
esac

# ===========================================================================
log "STAGE 1 -- the credentials load and are the right ones"
# ===========================================================================
# Read from Secret Manager, not from your shell, unless you overrode them:
# what matters is whether the values THE SERVICE READS work, which is a
# different question from whether the values you pasted work.
_secret() {
  gcloud secrets versions access latest --secret="$1" 2>/dev/null || true
}
CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-$(_secret google-oauth-client-id)}"
CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-$(_secret google-oauth-client-secret)}"
REFRESH_TOKEN="${GOOGLE_OAUTH_REFRESH_TOKEN:-$(_secret google-oauth-refresh-token)}"

[ -n "$CLIENT_ID" ] && [ -n "$CLIENT_SECRET" ] && [ -n "$REFRESH_TOKEN" ] ||
  die "the OAuth credentials are missing. Secret Manager has no readable
  google-oauth-client-id / -client-secret / -refresh-token in ${PROJECT_ID},
  and none of GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN is set here.
  A human has to mint the refresh token once -- see infra/OAUTH.md steps 1-4 --
  then land it with go_live.sh. No script can perform that consent click."

# `printf` is a shell builtin, so the secrets never appear in the process table
# the way `curl -d client_secret=...` would. The response body is captured but
# NEVER printed: it contains a live access token. Only the `scope`, the
# `expires_in`, and (on failure) the `error` fields are ever echoed.
_token_response="$(
  printf 'client_id=%s&client_secret=%s&refresh_token=%s&grant_type=refresh_token' \
    "$CLIENT_ID" "$CLIENT_SECRET" "$REFRESH_TOKEN" |
  curl -sS -m 30 -X POST https://oauth2.googleapis.com/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-binary @- -w '\n%{http_code}' || true
)"
_token_code="$(printf '%s' "$_token_response" | tail -n1)"
_token_body="$(printf '%s' "$_token_response" | sed '$d')"

if [ "$_token_code" != "200" ]; then
  _err="$(printf '%s' "$_token_body" | grep -o '"error[^"]*": *"[^"]*"' || true)"
  die "the refresh token was REFUSED by Google (HTTP ${_token_code:-no response}).
  Google said:
$(body "${_err:-see https://oauth2.googleapis.com/token}")

  'invalid_grant' almost always means one of: the token was revoked at
  https://myaccount.google.com/permissions ; the OAuth app is still in Testing
  status and the demo account was removed from its test users; or the client id
  and refresh token come from two different OAuth clients. Re-mint:
    python services/intake/scripts/mint_oauth_token.py --client-secrets <json>
  then re-run go_live.sh and this script."
fi
_scope="$(printf '%s' "$_token_body" | grep -o '"scope": *"[^"]*"' || true)"
_expires="$(printf '%s' "$_token_body" | grep -o '"expires_in": *[0-9]*' || true)"
ok "refresh token exchanged for an access token (${_expires:-expires_in unknown})"
case "$_scope" in
  *gmail.readonly*) ok "granted ${_scope}" ;;
  *) die "the token works but was NOT granted https://www.googleapis.com/auth/gmail.readonly.
  Every Gmail call will 403 with 'Request had insufficient authentication scopes',
  which reads like a broken token and is not one. Granted:
$(body "${_scope:-(no scope field in the response)}")

  Re-mint with the full scope set -- mint_oauth_token.py's SCOPES is the source
  of truth (gmail.readonly + calendar + drive.file) -- after revoking the old
  grant at https://myaccount.google.com/permissions." ;;
esac

# ===========================================================================
log "STAGE 2 -- Gmail's own service account can publish to the topic"
# ===========================================================================
# Checked BEFORE the watch, not after, on purpose. `users.watch` sends a test
# message to the topic at registration time and REFUSES the watch outright if
# that publish is denied, with a 400 whose text ("User not authorized to
# perform this action") names neither the topic nor the principal. Verifying
# the binding first turns that confusing failure into a precise one. It was
# genuinely absent on this project as of 2026-08-26 (infra/setup.sh now creates
# it) and it is the single most likely thing to be wrong here.
gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 ||
  die "the Pub/Sub topic ${TOPIC} does not exist, so Gmail has nowhere to publish.
  Run:  PROJECT_ID=${PROJECT_ID} ./infra/setup.sh"
_policy="$(gcloud pubsub topics get-iam-policy "$TOPIC" --format=json 2>&1 || true)"
case "$_policy" in
  *"$GMAIL_PUSH_SA"*) ok "${GMAIL_PUSH_SA} holds a binding on ${TOPIC}" ;;
  *) die "${GMAIL_PUSH_SA} has NO binding on ${TOPIC}.
  users.watch will be refused with a 400 that says only 'User not authorized to
  perform this action'. Gmail does not publish as you -- it publishes as its own
  fixed global system account, and no project-level grant covers it.
  Current policy:
$(body "$_policy")

  Fix (infra/setup.sh does this idempotently):
    gcloud pubsub topics add-iam-policy-binding ${TOPIC} \\
      --member=serviceAccount:${GMAIL_PUSH_SA} --role=roles/pubsub.publisher" ;;
esac
case "$_policy" in
  *roles/pubsub.publisher*) ok "roles/pubsub.publisher is present on ${TOPIC}" ;;
  *) die "${GMAIL_PUSH_SA} is bound on ${TOPIC} but NOT as roles/pubsub.publisher.
  Policy:
$(body "$_policy")" ;;
esac

# ===========================================================================
log "STAGE 3 -- users.watch is accepted and returns a historyId"
# ===========================================================================
# Called through the DEPLOYED service rather than directly against Gmail, so a
# pass here proves the whole chain the live path uses: the secrets Cloud Run
# mounted, intake/google_auth.py, _service(), and Gmail's answer. Calling
# gmail.googleapis.com from this laptop would prove only that the token works,
# which stage 1 already established.
#
# This is the one genuinely mutating call in the script, and it is idempotent:
# users.watch simply re-arms the 7-day watch and rewrites the stored cursor.
_watch_out="$(mktemp)"
trap 'rm -f "$_watch_out"' EXIT
_watch_code="$(curl -sS -m 60 -X POST "${INTAKE_URL}/gmail/watch/renew" \
  -o "$_watch_out" -w '%{http_code}' || true)"
_watch_body="$(cat "$_watch_out" 2>/dev/null || true)"
[ "$_watch_code" = "200" ] ||
  die "POST ${INTAKE_URL}/gmail/watch/renew returned ${_watch_code:-no response}, not 200.
  Gmail is NOT watching the inbox -- the feature is dead, not degraded.
  Response body:
$(body "$_watch_body")

  Read that body first; it is Gmail's own words. Common causes, in order:
    * 'User not authorized to perform this action'  -> stage 2's grant is
      missing on the topic Cloud Run is configured with (not necessarily
      ${TOPIC} -- check TOPIC_INTAKE_EMAIL_RECEIVED on the revision).
    * MissingCredentialsError                        -> the secrets are not
      mounted on the CURRENT revision; re-run go_live.sh.
    * invalid_grant                                  -> the refresh token in
      Secret Manager differs from the one stage 1 just validated; re-run
      go_live.sh with the freshly minted values.
  Full request log:
    gcloud run services logs read ef-intake --region=${REGION} --limit=50"

HISTORY_ID="$(printf '%s' "$_watch_body" | grep -o '"historyId": *"\{0,1\}[0-9]*' |
  grep -o '[0-9]*$' || true)"
[ -n "$HISTORY_ID" ] ||
  die "users.watch returned 200 but no historyId, which should be impossible --
  gmail.v1's WatchResponse schema has exactly two fields, historyId and
  expiration. Body:
$(body "$_watch_body")"
_expiration="$(printf '%s' "$_watch_body" | grep -o '"expiration": *"\{0,1\}[0-9]*' |
  grep -o '[0-9]*$' || true)"
ok "users.watch accepted -- historyId=${HISTORY_ID}"
if [ -n "$_expiration" ]; then
  # Gmail returns epoch MILLIseconds. Printed as a date because "1756829400000"
  # tells an operator nothing about whether the watch is about to lapse.
  _exp_s=$(( _expiration / 1000 ))
  _exp_h="$(date -u -r "$_exp_s" '+%Y-%m-%d %H:%M UTC' 2>/dev/null ||
            date -u -d "@${_exp_s}" '+%Y-%m-%d %H:%M UTC' 2>/dev/null || echo "$_expiration")"
  ok "watch expires ${_exp_h} (Cloud Scheduler job ef-gmail-watch-renew re-arms it daily)"
fi

# ===========================================================================
log "STAGE 4 -- the subscription actually delivers to the push endpoint"
# ===========================================================================
# This project's defect #2: all five subscriptions sat PULL with nothing
# subscribed, so every notification landed in a queue nobody read while every
# response said 200. A topic without a live push subscription is inert.
_sub="$(gcloud pubsub subscriptions describe "$SUBSCRIPTION" --format=json 2>&1 || true)"
case "$_sub" in
  *pushEndpoint*) : ;;
  *NOT_FOUND*|*"was not found"*)
    die "the subscription ${SUBSCRIPTION} does not exist. Gmail's notification would
  be published to ${TOPIC} and delivered to nobody.
    PROJECT_ID=${PROJECT_ID} ./infra/setup.sh" ;;
  *)
    die "${SUBSCRIPTION} exists but has NO pushEndpoint -- it is a PULL subscription
  with no subscriber, which is this project's defect #2 verbatim: every Gmail
  notification lands in a queue nobody reads, and every component reports
  success. Subscription:
$(body "$_sub")

  Fix:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh intake
   or:  gcloud pubsub subscriptions update ${SUBSCRIPTION} \\
          --push-endpoint=${INTAKE_URL}/pubsub/gmail" ;;
esac
_push_endpoint="$(printf '%s' "$_sub" | grep -o '"pushEndpoint": *"[^"]*"' |
  sed 's/.*: *"//; s/"$//' || true)"
case "$_push_endpoint" in
  "${INTAKE_URL}/pubsub/gmail") ok "${SUBSCRIPTION} -> ${_push_endpoint}" ;;
  *) die "${SUBSCRIPTION} pushes to ${_push_endpoint:-(unreadable)}, which is not this
  service's Gmail route. Expected: ${INTAKE_URL}/pubsub/gmail
  A stale endpoint from an earlier revision URL delivers every notification to
  a 404 and looks, from the console, exactly like Gmail never fired.
  Fix:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh intake" ;;
esac

# ===========================================================================
log "STAGE 5 -- a notification published to the topic reaches /pubsub/gmail"
# ===========================================================================
# Publishes ONE synthetic notification shaped exactly like Gmail's own
# (https://developers.google.com/gmail/api/guides/push -- {"emailAddress",
# "historyId"}), carrying the historyId stage 3 just returned. Because that is
# also the cursor stage 3 just stored, history.list diffs it against itself:
# nothing is fetched, nothing is stored, nothing is published downstream. What
# it proves is the delivery path -- topic -> subscription -> Cloud Run OIDC ->
# route -- which is exactly the segment that has silently failed here before.
# `emailAddress` is present in Gmail's real notification and ignored by
# `pipeline.process_gmail_push`, which reads only `historyId`. It is included so
# the probe is byte-shaped like the real thing rather than a special case the
# handler might treat differently; the address itself is synthetic (§0.6).
_probe_payload="{\"emailAddress\":\"${DEMO_EMAIL_ADDRESS:-verify-live@example.invalid}\",\"historyId\":${HISTORY_ID}}"
_published_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
gcloud pubsub topics publish "$TOPIC" --message="$_probe_payload" >/dev/null 2>&1 ||
  die "could not publish a test notification to ${TOPIC}.
  Your own account needs roles/pubsub.publisher on it to run this stage:
    gcloud pubsub topics add-iam-policy-binding ${TOPIC} \\
      --member=user:\$(gcloud config get-value account) --role=roles/pubsub.publisher"
ok "published a synthetic notification (historyId=${HISTORY_ID}, a deliberate no-op)"

_filter="resource.type=cloud_run_revision"
_filter="${_filter} AND resource.labels.service_name=ef-intake"
_filter="${_filter} AND httpRequest.requestUrl:\"/pubsub/gmail\""
_filter="${_filter} AND timestamp>=\"${_published_at}\""

_status=""
_waited=0
printf '    waiting up to %ss for the push to arrive' "$PUSH_WAIT_SECONDS"
while [ "$_waited" -lt "$PUSH_WAIT_SECONDS" ]; do
  _status="$(gcloud logging read "$_filter" --limit=1 --order=desc \
    --format='value(httpRequest.status)' 2>/dev/null | head -n1 || true)"
  [ -n "$_status" ] && break
  printf '.'
  sleep 5
  _waited=$(( _waited + 5 ))
done
printf '\n'

if [ -z "$_status" ]; then
  die "no request to /pubsub/gmail appeared in ef-intake's logs within ${PUSH_WAIT_SECONDS}s.
  The notification was published to ${TOPIC} at ${_published_at} and never
  arrived. Stages 2 and 4 passed, so the topic and the push endpoint are both
  configured -- which leaves delivery itself: the push subscription's OIDC
  service account, or ef-intake refusing unauthenticated invocations.
  Look at:
    gcloud pubsub subscriptions describe ${SUBSCRIPTION}   # pushConfig.oidcToken
    gcloud run services describe ef-intake --region=${REGION} \\
      --format='value(spec.template.spec.serviceAccountName)'
    gcloud logging read 'resource.labels.service_name=\"ef-intake\"' --limit=20 --freshness=10m
  If Cloud Logging is simply lagging, re-run this script -- stage 5 is safe to
  repeat, and it is the only stage that can produce a false negative this way."
fi

case "$_status" in
  2*) ok "push delivered -- /pubsub/gmail answered HTTP ${_status}" ;;
  *)
    _errs="$(gcloud logging read "${_filter} AND severity>=ERROR" --limit=5 --order=desc \
      --format='value(textPayload,jsonPayload.message)' 2>/dev/null || true)"
    die "/pubsub/gmail answered HTTP ${_status}. The notification reached the service
  and the handler failed. This is a code or configuration failure inside
  ef-intake, not a delivery problem. Its own log lines:
$(body "${_errs:-(no ERROR-severity lines; widen the search below)}")

  Full context:
    gcloud logging read 'resource.labels.service_name=\"ef-intake\"' --limit=50 --freshness=10m" ;;
esac

# ===========================================================================
log "STAGE 6 -- an emailed attachment is in GCS"
# ===========================================================================
gcloud storage ls "gs://${DOCS_BUCKET}/" >/dev/null 2>&1 ||
  die "the documents bucket gs://${DOCS_BUCKET} does not exist or is not readable
  by $(gcloud config get-value account 2>/dev/null). Every attachment upload
  would fail after Gmail had already worked.
    PROJECT_ID=${PROJECT_ID} ./infra/setup.sh"
ok "gs://${DOCS_BUCKET} exists"

# Layout is intake/{gmail_message_id}/{mime_part_id}/{filename}
# (services/intake/intake/storage.py).
_objects="$(gcloud storage ls "gs://${DOCS_BUCKET}/intake/**" 2>/dev/null | head -n 10 || true)"
if [ -n "$_objects" ]; then
  ok "attachments ingested from Gmail:"
  body "$_objects"
else
  soft_or_die "nothing under gs://${DOCS_BUCKET}/intake/ -- no emailed attachment has
  been stored yet. Send a real email with a PDF attachment to the demo inbox and
  re-run with --after-email. If you already did, the message may not be in INBOX
  (history.list is filtered to INBOX on purpose -- drafts, sent mail and spam are
  ignored), or it may carry no attachment Gmail reports as application/pdf.
  Watch it happen:
    gcloud run services logs tail ef-intake --region=${REGION}"
fi

# ===========================================================================
log "STAGE 7 -- the case exists"
# ===========================================================================
# services/intake has no Firestore grant by design (ef-intake holds no
# roles/datastore.user -- see intake/dedupe.py), so the case is created by
# agent-core's document-added handler from the event's own gcs_uri / filename /
# raw_text. Read through ef-api rather than the Firestore console because that
# is the same path the dashboard uses, so a pass here means the demo screen
# will show it.
API_URL="$(gcloud run services describe ef-api --region="$REGION" \
  --format='value(status.url)' 2>/dev/null || true)"
if [ -z "$API_URL" ]; then
  warn "ef-api is not deployed, so the case cannot be checked through the dashboard's
       own path. Inspect Firestore directly in the console: collection 'cases',
       document id 'case-<gmail thread id>'."
else
  _cases="$(curl -sS -m 30 "${API_URL}/cases" || true)"
  case "$_cases" in
    *'"case-'*)
      ok "ef-api reports at least one Gmail-sourced case (id prefixed 'case-<threadId>'):"
      body "$(printf '%s' "$_cases" | grep -o '"case-[^"]*"' | sort -u | head -n 5)" ;;
    "")
      soft_or_die "GET ${API_URL}/cases returned nothing. Check ef-api is healthy:
    curl -i ${API_URL}/cases" ;;
    *)
      soft_or_die "GET ${API_URL}/cases returned no case with a 'case-<threadId>' id, so
  agent-core has not created a case from a Gmail-sourced document yet.
  If stage 6 found an object in GCS but this is still empty, the break is
  between intake's publish and agent-core's handler -- look there, not at Gmail:
    gcloud logging read 'resource.labels.service_name=\"ef-agent-core\"' --limit=50 --freshness=15m
    gcloud pubsub subscriptions describe ef-document-added" ;;
  esac
fi

# ===========================================================================
printf '\n\033[1;32mAll checked stages passed.\033[0m\n'
if [ "$AFTER_EMAIL" = "1" ]; then
  cat <<EOF

Gmail intake is live and has been observed end to end. What that does and does
not cover is written up in services/intake/tests/test_gmail_transport.py's
docstring -- in particular, this script has now exercised the OAuth-gated calls
that no test can.
EOF
else
  cat <<EOF

Next, and it is the only step that proves the whole thing:

  1. Email a PDF bill to the demo Gmail account. Use fixtures/generated/cases/
     case_01_uninsured_gfe_ca/documents/bill.pdf -- synthetic, and the same
     bytes the test suite pushes through the Gmail transport fake.
  2. Watch it land:
       gcloud run services logs tail ef-intake --region=${REGION}
  3. Re-run this script with --after-email:
       PROJECT_ID=${PROJECT_ID} $0 --after-email
EOF
fi
