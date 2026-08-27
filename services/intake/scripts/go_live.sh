#!/usr/bin/env bash
# RELAY (persona 4), WO7 -- "make Gmail intake genuinely work end to end."
#
# WHAT THIS SCRIPT IS AND ISN'T
# ------------------------------
# OWNERSHIP, amended 2026-08-26 (FORGE). This header used to say that
# `infra/setup.sh`/`infra/deploy.sh` never convert `ef-intake-email` from pull
# to push and never provision the Cloud Scheduler job. That was true when this
# script was written and became false one minute later, at commit `695ac88`:
# `deploy.sh` now does both, and `setup.sh` now creates the secret NAMES with
# per-secret IAM and grants gmail-api-push its publisher role on the topic.
#
# Stale ownership comments are not cosmetic here. This project's defect #2 was
# exactly one: `setup.sh`'s comment promised `deploy.sh` would convert the
# subscriptions to push, `deploy.sh` never did, and five subscriptions sat
# PULL with no subscriber while every service reported success.
#
# So, explicitly, as of today:
#   infra/setup.sh   OWNS  topics, subscriptions, service accounts, secret
#                          NAMES + their per-secret IAM, the gmail-api-push
#                          publisher grant, dead-letter IAM, budget.
#   infra/deploy.sh  OWNS  Cloud Run services, their literal env vars
#                          (including GCS_DOCUMENTS_BUCKET), push endpoints,
#                          and the Cloud Scheduler renewal job.
#   this script      OWNS  secret VALUES (the OAuth token and vendor keys),
#                          wiring those secrets into the two services, and
#                          starting the first Gmail watch.
#
# The overlap that remains is deliberate and idempotent: this script re-asserts
# the push endpoint and the scheduler job because it must work even if someone
# runs it against a project where only `setup.sh` has been run. Where it
# overlaps it must not DRIFT -- note that its `jobs update http` omits
# `--schedule`, so `deploy.sh` remains the source of truth for the cadence.
#
# IDEMPOTENT BY CONSTRUCTION, same convention as setup.sh: safe to re-run
# after minting a new token, rotating a vendor key, or a fresh `deploy.sh
# all`.
#
# USAGE
# -----
#   PROJECT_ID=everyfront-hack-2026 \
#   GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \
#   GOOGLE_OAUTH_REFRESH_TOKEN=... \
#   ./services/intake/scripts/go_live.sh
#
# Every other env var below is OPTIONAL -- the script tells you exactly what
# it skipped and why. Run `mint_oauth_token.py` first (see this directory) to
# get the three GOOGLE_OAUTH_* values; see services/intake/README.md for the
# full numbered runbook.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
skip() { printf '    \033[2m--\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mwarn\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null || die "gcloud not installed"
gcloud config set project "$PROJECT_ID" >/dev/null

# ---------------------------------------------------------------- secrets
# name -> env var. Every one of these is read straight from the environment
# and NEVER echoed or logged -- only its secret-manager NAME is printed.
declare -a SECRET_SPECS=(
  "google-oauth-client-id:GOOGLE_OAUTH_CLIENT_ID"
  "google-oauth-client-secret:GOOGLE_OAUTH_CLIENT_SECRET"
  "google-oauth-refresh-token:GOOGLE_OAUTH_REFRESH_TOKEN"
  "phaxio-api-key:PHAXIO_API_KEY"
  "phaxio-api-secret:PHAXIO_API_SECRET"
  "lob-api-key:LOB_API_KEY"
  "demo-fax-allowlist:DEMO_FAX_ALLOWLIST"
  "demo-mail-allowlist:DEMO_MAIL_ALLOWLIST"
  "google-calendar-id:GOOGLE_CALENDAR_ID"
  "google-drive-root-folder-id:GOOGLE_DRIVE_ROOT_FOLDER_ID"
  "google-drive-advocate-email:GOOGLE_DRIVE_ADVOCATE_EMAIL"
)

# Reject a value that is obviously not the credential it claims to be, BEFORE
# writing it to Secret Manager and redeploying two services around it.
#
# 2026-08-26: the three placeholders from a copy-pasted command
# (`GOOGLE_OAUTH_CLIENT_ID='<client id>'`) were accepted verbatim, stored as
# secret versions, wired into ef-intake and ef-agent-core, and only surfaced
# four steps later as an opaque HTTP 500 from the watch call, whose real cause
# -- `invalid_client: The OAuth client was not found.` -- was buried in a Cloud
# Run traceback. Single quotes meant the shell never complained. Every one of
# those steps was working correctly; the input was wrong and nothing looked at
# it. Checking shape here costs nothing and turns a 4-step debug into one line.
_reject() { die "GOOGLE_OAUTH_$1 $2
  Re-run with the real values printed by mint_oauth_token.py. Do not paste the
  placeholder text from a command template."; }

if [ -n "${GOOGLE_OAUTH_CLIENT_ID:-}" ]; then
  case "$GOOGLE_OAUTH_CLIENT_ID" in
    *[[:space:]]*|*'<'*|*'>'*) _reject CLIENT_ID "contains whitespace or angle brackets -- that is placeholder text, not a client id." ;;
    *.apps.googleusercontent.com) : ;;
    *) _reject CLIENT_ID "does not end in .apps.googleusercontent.com, so Google's token endpoint will reject it with 'invalid_client: The OAuth client was not found.'" ;;
  esac
fi
if [ -n "${GOOGLE_OAUTH_CLIENT_SECRET:-}" ]; then
  case "$GOOGLE_OAUTH_CLIENT_SECRET" in
    *[[:space:]]*|*'<'*|*'>'*) _reject CLIENT_SECRET "contains whitespace or angle brackets -- that is placeholder text, not a client secret." ;;
  esac
  [ "${#GOOGLE_OAUTH_CLIENT_SECRET}" -lt 20 ] && _reject CLIENT_SECRET "is only ${#GOOGLE_OAUTH_CLIENT_SECRET} characters; a real one is far longer."
fi
if [ -n "${GOOGLE_OAUTH_REFRESH_TOKEN:-}" ]; then
  case "$GOOGLE_OAUTH_REFRESH_TOKEN" in
    *[[:space:]]*|*'<'*|*'>'*) _reject REFRESH_TOKEN "contains whitespace or angle brackets -- that is placeholder text, not a refresh token." ;;
  esac
  [ "${#GOOGLE_OAUTH_REFRESH_TOKEN}" -lt 30 ] && _reject REFRESH_TOKEN "is only ${#GOOGLE_OAUTH_REFRESH_TOKEN} characters; a real one is far longer."
fi

log "Secret Manager (agreement §2.4 -- no secrets in code, ever)"
declare -a CREATED_SECRETS=()
for spec in "${SECRET_SPECS[@]}"; do
  name="${spec%%:*}"
  var="${spec#*:}"
  value="${!var:-}"
  if [ -z "$value" ]; then
    skip "$name ($var not set -- see README for what stays off without it)"
    continue
  fi
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    ok "$name (new version)"
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- \
      --replication-policy=automatic >/dev/null
    ok "$name (created)"
  fi
  CREATED_SECRETS+=("$name")
done

if [ "${#CREATED_SECRETS[@]}" -eq 0 ]; then
  die "no secrets provided -- at minimum set GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN
  (run mint_oauth_token.py first; see services/intake/README.md)"
fi

# ------------------------------------------------------- Cloud Run env wiring
# ef-intake and ef-agent SAs already hold roles/secretmanager.secretAccessor
# (infra/setup.sh's SA_RECORDS) -- no new IAM grant needed to READ a secret
# this script just created in the same project. `--update-secrets` is
# idempotent: re-running with the same mapping is a no-op; a NEW secret
# version (above) takes effect on the next revision this triggers.
log "Wiring secrets into deployed Cloud Run services"

_has_secret() { for s in "${CREATED_SECRETS[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

_service_exists() { gcloud run services describe "$1" --region="$REGION" >/dev/null 2>&1; }

INTAKE_SECRET_MAP=""
for pair in "GOOGLE_OAUTH_CLIENT_ID:google-oauth-client-id" \
            "GOOGLE_OAUTH_CLIENT_SECRET:google-oauth-client-secret" \
            "GOOGLE_OAUTH_REFRESH_TOKEN:google-oauth-refresh-token"; do
  envvar="${pair%%:*}"; secret="${pair#*:}"
  _has_secret "$secret" || continue
  INTAKE_SECRET_MAP="${INTAKE_SECRET_MAP:+$INTAKE_SECRET_MAP,}${envvar}=${secret}:latest"
done

AGENT_SECRET_MAP=""
for pair in "GOOGLE_OAUTH_CLIENT_ID:google-oauth-client-id" \
            "GOOGLE_OAUTH_CLIENT_SECRET:google-oauth-client-secret" \
            "GOOGLE_OAUTH_REFRESH_TOKEN:google-oauth-refresh-token" \
            "PHAXIO_API_KEY:phaxio-api-key" \
            "PHAXIO_API_SECRET:phaxio-api-secret" \
            "LOB_API_KEY:lob-api-key" \
            "DEMO_FAX_ALLOWLIST:demo-fax-allowlist" \
            "DEMO_MAIL_ALLOWLIST:demo-mail-allowlist" \
            "GOOGLE_CALENDAR_ID:google-calendar-id" \
            "GOOGLE_DRIVE_ROOT_FOLDER_ID:google-drive-root-folder-id" \
            "GOOGLE_DRIVE_ADVOCATE_EMAIL:google-drive-advocate-email"; do
  envvar="${pair%%:*}"; secret="${pair#*:}"
  _has_secret "$secret" || continue
  AGENT_SECRET_MAP="${AGENT_SECRET_MAP:+$AGENT_SECRET_MAP,}${envvar}=${secret}:latest"
done

# GCS_DOCUMENTS_BUCKET is not a secret (it's just a name), but nothing in
# deploy.sh sets it on ANY service today -- services/intake/intake/storage.py
# does a hard `os.environ["GCS_DOCUMENTS_BUCKET"]` (no default: fails loud,
# by design) and would KeyError on every real attachment upload without this.
# Bucket name matches infra/setup.sh's own naming exactly.
DOCS_BUCKET="ef-documents-${PROJECT_ID}"

if _service_exists ef-intake; then
  if [ -n "$INTAKE_SECRET_MAP" ]; then
    gcloud run services update ef-intake --region="$REGION" \
      --update-secrets="$INTAKE_SECRET_MAP" \
      --update-env-vars="GCS_DOCUMENTS_BUCKET=${DOCS_BUCKET}" \
      --quiet >/dev/null
    ok "ef-intake: secrets + GCS_DOCUMENTS_BUCKET=${DOCS_BUCKET}"
  else
    gcloud run services update ef-intake --region="$REGION" \
      --update-env-vars="GCS_DOCUMENTS_BUCKET=${DOCS_BUCKET}" --quiet >/dev/null
    warn "ef-intake: only GCS_DOCUMENTS_BUCKET wired -- no OAuth secrets were provided"
  fi
else
  die "ef-intake is not deployed, so its secrets cannot be wired.
  Run:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh intake
  then re-run this script. Order matters: deploy FIRST, this script SECOND."
fi

if _service_exists ef-agent-core; then
  env_update="GCS_DOCUMENTS_BUCKET=${DOCS_BUCKET}"
  if [ -n "$AGENT_SECRET_MAP" ]; then
    gcloud run services update ef-agent-core --region="$REGION" \
      --update-secrets="$AGENT_SECRET_MAP" --update-env-vars="$env_update" --quiet >/dev/null
    ok "ef-agent-core: secrets + GCS_DOCUMENTS_BUCKET=${DOCS_BUCKET}"
  else
    gcloud run services update ef-agent-core --region="$REGION" \
      --update-env-vars="$env_update" --quiet >/dev/null
    warn "ef-agent-core: only GCS_DOCUMENTS_BUCKET wired -- vendor/calendar/drive secrets stay off"
  fi
else
  die "ef-agent-core is not deployed, so its secrets cannot be wired.
  Run:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh agent-core
  then re-run this script. Order matters: deploy FIRST, this script SECOND."
fi

# ------------------------------------------------------- Pub/Sub push wiring
# infra/setup.sh creates `ef-intake-email` as a PULL subscription on the
# `intake.email.received` topic and its own comment says deploy.sh converts
# it to push once the Cloud Run URL exists -- deploy.sh does not actually do
# this (verified: no `push-endpoint`/`--update` calls anywhere in it). Without
# this, Gmail's watch notification lands in Pub/Sub and NOTHING ever delivers
# it to `/pubsub/gmail` -- the single most silent way this whole feature
# could look wired and do nothing. `--push-auth-service-account` is set even
# though `ef-intake` also allows unauthenticated calls (belt + suspenders;
# costs nothing since ef-intake already grants itself run.invoker implicitly
# via --allow-unauthenticated).
log "Converting ef-intake-email to a push subscription"
if _service_exists ef-intake; then
  INTAKE_URL="$(gcloud run services describe ef-intake --region="$REGION" \
    --format='value(status.url)')"
  if gcloud pubsub subscriptions describe ef-intake-email >/dev/null 2>&1; then
    gcloud pubsub subscriptions update ef-intake-email \
      --push-endpoint="${INTAKE_URL}/pubsub/gmail" \
      --push-auth-service-account="ef-intake@${PROJECT_ID}.iam.gserviceaccount.com" \
      >/dev/null
    ok "ef-intake-email -> ${INTAKE_URL}/pubsub/gmail"
  else
    die "the ef-intake-email subscription does not exist, so Gmail pushes have
  nowhere to land. Run:  PROJECT_ID=${PROJECT_ID} ./infra/setup.sh
  then re-run this script."
  fi
else
  warn "ef-intake not deployed -- cannot wire a push endpoint that doesn't exist yet"
fi

# ------------------------------------------------------- Cloud Scheduler
# WO1: "Handle watch renewal (7-day expiry) with a Cloud Scheduler job."
# Daily, not weekly: `POST /gmail/watch/renew` is idempotent and re-arms the
# watch for another 7 days regardless of when it's called (main.py just
# calls users.watch again) -- running it daily costs nothing and removes any
# risk of a scheduler hiccup on day 7 silently ending the demo's live intake.
log "Cloud Scheduler: daily Gmail watch renewal"
if _service_exists ef-intake; then
  INTAKE_URL="$(gcloud run services describe ef-intake --region="$REGION" \
    --format='value(status.url)')"
  if gcloud scheduler jobs describe ef-gmail-watch-renew --location="$REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http ef-gmail-watch-renew --location="$REGION" \
      --uri="${INTAKE_URL}/gmail/watch/renew" --http-method=POST \
      --oidc-service-account-email="ef-intake@${PROJECT_ID}.iam.gserviceaccount.com" \
      >/dev/null
    ok "updated ef-gmail-watch-renew -> ${INTAKE_URL}/gmail/watch/renew"
  else
    gcloud scheduler jobs create http ef-gmail-watch-renew --location="$REGION" \
      --schedule="0 3 * * *" --uri="${INTAKE_URL}/gmail/watch/renew" --http-method=POST \
      --oidc-service-account-email="ef-intake@${PROJECT_ID}.iam.gserviceaccount.com" \
      >/dev/null
    ok "created ef-gmail-watch-renew (daily @ 03:00) -> ${INTAKE_URL}/gmail/watch/renew"
  fi

  # Kick off the FIRST watch now -- otherwise nothing is watching the inbox
  # until tomorrow's scheduler run, and every step above was for nothing in
  # the meantime.
  if [ -n "$INTAKE_SECRET_MAP" ]; then
    log "Starting the initial Gmail watch"
    # THE pass/fail line for this entire script. `die`, not `warn`: everything
    # above is preparation, and a failure here means the mailbox is not being
    # watched at all -- the feature is dead, not degraded. This used to warn
    # and let the script reach "Done." and exit 0, which is this project's
    # signature failure: a green transcript over a feature that does nothing.
    _watch_code="$(curl -sS -X POST "${INTAKE_URL}/gmail/watch/renew" \
      -o /tmp/watch_result.json -w '%{http_code}' || true)"
    if [ "$_watch_code" = "200" ]; then
      ok "watch started: $(cat /tmp/watch_result.json)"
    else
      # Echo the body. The old version discarded it, which mattered: the most
      # likely failure is Gmail refusing the watch because
      # gmail-api-push@system.gserviceaccount.com lacks pubsub.publisher on
      # the topic, and that only says so in the response body.
      die "watch call returned ${_watch_code:-no response}, not 200. Gmail is NOT watching the inbox.
  Response body:
$(sed 's/^/    /' /tmp/watch_result.json 2>/dev/null || echo '    (empty)')

  If it mentions 'User not authorized to perform this action', the Gmail push
  grant is missing -- infra/setup.sh now creates it, so re-run:
    PROJECT_ID=${PROJECT_ID} ./infra/setup.sh
  Then re-run this script. Retry the call alone with:
    curl -sS -X POST ${INTAKE_URL}/gmail/watch/renew"
    fi
  else
    die "no OAuth secrets were wired this run, so the initial watch was skipped.
  Gmail intake is NOT live. Set GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN
  (see infra/OAUTH.md) and re-run."
  fi
else
  die "ef-intake is not deployed, so there is no URL to watch against.
  Run:  PROJECT_ID=${PROJECT_ID} ./infra/deploy.sh intake
  then re-run this script. Deploy BEFORE this script, never after -- deploy.sh
  sets the service's literal env vars and a later deploy would drop what this
  script wired."
fi

log "Done. Verify with:"
cat <<EOF
  gcloud secrets list --filter="name:google-oauth OR name:phaxio OR name:lob"
  gcloud run services describe ef-intake --region=${REGION} --format='value(spec.template.spec.containers[0].env)'
  gcloud pubsub subscriptions describe ef-intake-email
  gcloud scheduler jobs describe ef-gmail-watch-renew --location=${REGION}
EOF
