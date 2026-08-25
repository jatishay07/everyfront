#!/usr/bin/env bash
# ATLAS (persona 1), work order 1 -- one-command project bootstrap.
#
# Acceptance (§4 persona 1): fresh GCP project -> setup.sh -> deploy.sh all ->
# hello-world responds on public URLs, under 30 minutes, NO CONSOLE CLICKS.
#
# IDEMPOTENT BY CONSTRUCTION. Every create is guarded by an existence check or
# uses a flag that tolerates re-runs. Running this twice must be a no-op, because
# it will be run twice -- once to bootstrap and again after every failure.
#
#   usage:  PROJECT_ID=everyfront-hack-2026 ./infra/setup.sh
#           PROJECT_ID=... BILLING_ACCOUNT=012403-XXXXXX-XXXXXX ./infra/setup.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID, e.g. PROJECT_ID=everyfront-hack-2026}"
REGION="${REGION:-us-central1}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
skip() { printf '    \033[2m--\033[0m   %s (exists)\n' "$*"; }
die()  { printf '\n\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
log "Preflight"
command -v gcloud >/dev/null || die "gcloud not installed"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || die "no active gcloud account -- run: gcloud auth login"
ok "authenticated as $(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)"

# ---------------------------------------------------------------- project
log "Project: $PROJECT_ID"
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  skip "project $PROJECT_ID"
else
  gcloud projects create "$PROJECT_ID" --name="Every Front" >/dev/null
  ok "created project $PROJECT_ID"
fi
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null
ok "default project and region set ($REGION)"

# ---------------------------------------------------------------- billing
# Cloud Run, Vertex, and Firestore all refuse to work without billing. Fail
# loudly here rather than 8 confusing API errors later.
log "Billing"
if [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null)" == "True" ]]; then
  skip "billing already linked"
else
  [[ -n "$BILLING_ACCOUNT" ]] || die \
"billing is not enabled on $PROJECT_ID and BILLING_ACCOUNT was not set.

  list your accounts:  gcloud billing accounts list
  then re-run:         PROJECT_ID=$PROJECT_ID BILLING_ACCOUNT=<id> $0"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
  ok "linked billing account $BILLING_ACCOUNT"
fi

# ---------------------------------------------------------------- APIs
# §1.3 requires Cloud Run + Pub/Sub + Firestore. The rest are what the services
# actually need to build and run.
log "Enabling APIs (slow on first run, ~2-3 min)"
APIS=(
  run.googleapis.com pubsub.googleapis.com firestore.googleapis.com
  storage.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com
  cloudbuild.googleapis.com artifactregistry.googleapis.com
  cloudscheduler.googleapis.com gmail.googleapis.com
  billingbudgets.googleapis.com
  calendar-json.googleapis.com drive.googleapis.com
)
ENABLED="$(gcloud services list --enabled --format='value(config.name)')"
TO_ENABLE=()
for api in "${APIS[@]}"; do
  if grep -qx "$api" <<<"$ENABLED"; then skip "$api"; else TO_ENABLE+=("$api"); fi
done
if ((${#TO_ENABLE[@]})); then
  gcloud services enable "${TO_ENABLE[@]}" >/dev/null
  for api in "${TO_ENABLE[@]}"; do ok "$api"; done
fi

# ---------------------------------------------------------------- Firestore
log "Firestore (native mode)"
if gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  skip "default database"
else
  gcloud firestore databases create --location="$REGION" --type=firestore-native >/dev/null
  ok "created Firestore database in $REGION"
fi

# ---------------------------------------------------------------- indexes
# `GET /events` (contract §3.3) is a CROSS-CASE query: it reads the `events`
# subcollection of every case at once, ordered by time. Firestore refuses a
# collection-group query without a collection-group index and returns 400
# FailedPrecondition, which surfaces as a 500 from the API -- so the live
# activity feed, the screen the demo is built around, was broken on every
# request until this existed.
#
# gcloud's `indexes fields update` only accepts order/array-config and cannot
# set queryScope, so this goes through the Firestore Admin REST API directly.
log "Firestore indexes"
_index_payload='{"indexConfig":{"indexes":[
  {"queryScope":"COLLECTION_GROUP","fields":[{"fieldPath":"ts","order":"DESCENDING"}]},
  {"queryScope":"COLLECTION_GROUP","fields":[{"fieldPath":"ts","order":"ASCENDING"}]},
  {"queryScope":"COLLECTION","fields":[{"fieldPath":"ts","order":"DESCENDING"}]},
  {"queryScope":"COLLECTION","fields":[{"fieldPath":"ts","order":"ASCENDING"}]}
]}}'
if curl -sS -m 90 -X PATCH \
    "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/collectionGroups/events/fields/ts?updateMask=indexConfig" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" -d "$_index_payload" >/dev/null 2>&1; then
  ok "events.ts collection-group index (may take a few minutes to build)"
else
  printf '    \033[33mwarn\033[0m could not set the events.ts index -- GET /events will 500 until it exists\n'
fi

# ---------------------------------------------------------------- buckets
log "GCS buckets"
for bucket in "ef-documents-${PROJECT_ID}" "ef-datasets-${PROJECT_ID}"; do
  if gcloud storage buckets describe "gs://${bucket}" >/dev/null 2>&1; then
    skip "gs://${bucket}"
  else
    # Uniform access: no per-object ACLs. Patient documents must never be
    # public, and per-object ACLs are how that accident usually happens.
    gcloud storage buckets create "gs://${bucket}" \
      --location="$REGION" --uniform-bucket-level-access >/dev/null
    ok "gs://${bucket}"
  fi
done

# ---------------------------------------------------------------- Pub/Sub
# Contract §3.2 -- these five names are load-bearing. tests/test_contracts.py
# fails CI if they drift from the playbook.
log "Pub/Sub topics (contract §3.2)"
TOPICS=(intake.email.received case.document.added case.analysis.complete
        filing.requested filing.completed)
for t in "${TOPICS[@]}"; do
  if gcloud pubsub topics describe "$t" >/dev/null 2>&1; then
    skip "$t"
  else
    gcloud pubsub topics create "$t" >/dev/null; ok "$t"
  fi
done
# Dead-letter topic: agreement §2.3 requires handlers tolerate redelivery, but a
# poison message must not redeliver forever.
if gcloud pubsub topics describe dead-letter >/dev/null 2>&1; then
  skip "dead-letter"
else
  gcloud pubsub topics create dead-letter >/dev/null; ok "dead-letter"
fi

# ---------------------------------------------------------------- subscriptions
# ATLAS WO1 acceptance names "topics + push subscriptions". Topics alone are
# INERT: nothing is ever delivered until something subscribes. Created here as
# pull subscriptions; deploy.sh converts them to push once the Cloud Run URLs
# exist, because a push endpoint cannot be named before the service is deployed.
#
# Every one carries a dead-letter policy. Agreement §2.3 requires handlers to
# tolerate redelivery -- but a poison message must not redeliver forever.
log "Pub/Sub subscriptions"
SUB_RECORDS="
intake.email.received|ef-intake-email
case.document.added|ef-document-added
case.analysis.complete|ef-analysis-complete
filing.requested|ef-filing-requested
filing.completed|ef-filing-completed
"
while IFS='|' read -r topic sub; do
  [ -z "$topic" ] && continue
  if gcloud pubsub subscriptions describe "$sub" >/dev/null 2>&1; then
    skip "$sub"
  else
    gcloud pubsub subscriptions create "$sub" \
      --topic="$topic" \
      --ack-deadline=60 \
      --message-retention-duration=1d \
      --dead-letter-topic=dead-letter \
      --max-delivery-attempts=5 >/dev/null
    ok "$sub -> $topic"
  fi
done <<< "$SUB_RECORDS"

# The dead-letter topic needs a subscription of its own, or dead-lettered
# messages are silently dropped -- which defeats the point of having one.
if gcloud pubsub subscriptions describe ef-dead-letter >/dev/null 2>&1; then
  skip "ef-dead-letter"
else
  gcloud pubsub subscriptions create ef-dead-letter --topic=dead-letter \
    --message-retention-duration=7d >/dev/null
  ok "ef-dead-letter -> dead-letter"
fi

# ---------------------------------------------------------------- service accounts
log "Service accounts (least privilege)"
# macOS ships bash 3.2, which has no associative arrays. Parallel
# "name|roles" records keep this runnable on the dev machine AND on Cloud Shell.
SA_RECORDS="
ef-intake|roles/pubsub.publisher roles/storage.objectAdmin roles/secretmanager.secretAccessor
ef-agent|roles/pubsub.publisher roles/pubsub.subscriber roles/datastore.user roles/storage.objectAdmin roles/aiplatform.user roles/secretmanager.secretAccessor
ef-api|roles/datastore.user roles/pubsub.publisher roles/storage.objectViewer
ef-web|
"
PROJECT_NUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
while IFS='|' read -r sa roles; do
  [ -z "$sa" ] && continue
  email="${sa}@${PROJECT_ID}.iam.gserviceaccount.com"
  if gcloud iam service-accounts describe "$email" >/dev/null 2>&1; then
    skip "$sa"
  else
    gcloud iam service-accounts create "$sa" --display-name="Every Front ${sa}" >/dev/null
    ok "$sa"
  fi
  for role in $roles; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${email}" --role="$role" \
      --condition=None >/dev/null 2>&1 || true
  done
  [ -n "$roles" ] && ok "  roles bound to $sa"
done <<< "$SA_RECORDS"

# Pub/Sub push subscriptions need to mint OIDC tokens as the service account.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:service-${PROJECT_NUM}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" --condition=None >/dev/null 2>&1 || true
ok "Pub/Sub granted token-creator"

# ---------------------------------------------------------------- budget guard
# §6, amended 2026-08-21: tripwire at $50 against a $150 balance.
log "Budget alerts"
if [[ -n "$BILLING_ACCOUNT" ]]; then
  if gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
       --format='value(displayName)' 2>/dev/null | grep -qx "everyfront-guard"; then
    skip "budget everyfront-guard"
  else
    gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
      --display-name="everyfront-guard" --budget-amount=150USD \
      --threshold-rule=percent=0.33 --threshold-rule=percent=0.66 \
      --threshold-rule=percent=0.90 --threshold-rule=percent=1.0 >/dev/null 2>&1 \
      && ok "budget alerts at 33/66/90/100% of \$150" \
      || printf '    \033[33mwarn\033[0m budget create failed (needs billing.budgets.create) -- set it in the console\n'
  fi
else
  printf '    \033[33mwarn\033[0m BILLING_ACCOUNT unset, skipping budget alerts\n'
fi

log "Setup complete for $PROJECT_ID"
cat <<EOF

  next:  ./infra/deploy.sh all
  check: gcloud pubsub topics list
         gcloud storage ls
EOF
