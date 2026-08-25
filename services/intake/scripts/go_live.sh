#!/usr/bin/env bash
# RELAY (persona 4), WO7 -- "make Gmail intake genuinely work end to end."
#
# WHAT THIS SCRIPT IS AND ISN'T
# ------------------------------
# `infra/setup.sh`/`infra/deploy.sh` (ATLAS's files -- this script does not
# touch them) provision topics/subscriptions/service accounts/Cloud Run
# services but never create a Secret Manager secret, never wire one into a
# deployed service's env, never convert `ef-intake-email` from pull to push,
# and never provision the Cloud Scheduler job WO1 calls for -- so as of this
# work order a real email could arrive and NOTHING would happen: no push
# delivery, no OAuth credentials, no GCS_DOCUMENTS_BUCKET even set. This
# script closes every one of those gaps for `services/intake`'s own path by
# operating on already-created infra (secrets, an already-deployed Cloud Run
# service, an already-created subscription) with `gcloud`/`curl` -- it is
# deliberately NOT a change to ATLAS's `setup.sh`/`deploy.sh` (outside RELAY's
# owned paths; see the PR's HANDOFF for the equivalent permanent diff there).
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
  warn "ef-intake not deployed yet -- run infra/deploy.sh intake first, then re-run this script"
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
  warn "ef-agent-core not deployed yet -- run infra/deploy.sh agent-core first, then re-run"
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
    warn "ef-intake-email subscription does not exist -- run infra/setup.sh first"
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
    if curl -sS -X POST "${INTAKE_URL}/gmail/watch/renew" -o /tmp/watch_result.json -w '%{http_code}' \
        | grep -q '^200$'; then
      ok "watch started: $(cat /tmp/watch_result.json)"
    else
      warn "watch call did not return 200 -- inspect: curl -X POST ${INTAKE_URL}/gmail/watch/renew"
    fi
  else
    warn "skipping the initial watch call -- no OAuth secrets were wired this run"
  fi
else
  warn "ef-intake not deployed -- cannot schedule renewal against a URL that doesn't exist yet"
fi

log "Done. Verify with:"
cat <<EOF
  gcloud secrets list --filter="name:google-oauth OR name:phaxio OR name:lob"
  gcloud run services describe ef-intake --region=${REGION} --format='value(spec.template.spec.containers[0].env)'
  gcloud pubsub subscriptions describe ef-intake-email
  gcloud scheduler jobs describe ef-gmail-watch-renew --location=${REGION}
EOF
