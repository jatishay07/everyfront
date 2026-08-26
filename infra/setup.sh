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
warn() { printf '    \033[33mwarn\033[0m %s\n' "$*"; }
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

# Resolved once, here, because three later sections need it: the Pub/Sub
# service agent's address (dead-letter permissions + token-creator) and the
# budget/monitoring sections.
PROJECT_NUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
PUBSUB_AGENT="service-${PROJECT_NUM}@gcp-sa-pubsub.iam.gserviceaccount.com"

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
  warn "could not set the events.ts index -- GET /events will 500 until it exists"
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

# ------------------------------------------------- Gmail -> Pub/Sub publisher
# THE grant that makes Gmail intake work at all, and the single most commonly
# missed step in a Gmail-to-Pub/Sub setup. `users.watch` does NOT publish as
# the caller: Gmail publishes as its own fixed system account, which must hold
# roles/pubsub.publisher ON THIS EXACT TOPIC. No project-level binding we make
# elsewhere covers it, because the principal is not ours -- it is a single
# global account shared by every Gmail API user on earth.
#
# Worse, `users.watch` does not merely fail later: it sends a test message to
# the topic at watch-registration time and REFUSES the watch outright if the
# publish is denied --
#
#   400 FAILED_PRECONDITION -- "Error sending test message to Cloud PubSub
#   projects/<p>/topics/intake.email.received : User not authorized to
#   perform this action."
#
# so the mailbox is never watched at all. Confirmed absent live 2026-08-26
# (ATLAS infra audit): the topic's IAM policy was empty (`etag: ACAB`, zero
# bindings) and the string `gmail-api-push` appeared nowhere in this repo. It
# belongs in the idempotent bootstrap, not in a runbook step someone has to
# remember, because forgetting it produces the project's signature failure
# mode -- a green transcript over a completely dead feature.
GMAIL_PUSH_SA="gmail-api-push@system.gserviceaccount.com"
log "Gmail push publisher"
if gcloud pubsub topics get-iam-policy intake.email.received \
     --format='value(bindings.members)' 2>/dev/null | grep -q "$GMAIL_PUSH_SA"; then
  skip "gmail-api-push publisher on intake.email.received"
elif gcloud pubsub topics add-iam-policy-binding intake.email.received \
       --member="serviceAccount:${GMAIL_PUSH_SA}" \
       --role="roles/pubsub.publisher" >/dev/null 2>&1; then
  ok "gmail-api-push -> roles/pubsub.publisher on intake.email.received"
else
  die "could not grant roles/pubsub.publisher on intake.email.received to
  ${GMAIL_PUSH_SA}. Gmail's users.watch WILL be refused with a confusing
  'User not authorized to perform this action' until this binding exists,
  and Gmail intake will be silently dead. Retry:
    gcloud pubsub topics add-iam-policy-binding intake.email.received \\
      --member=serviceAccount:${GMAIL_PUSH_SA} --role=roles/pubsub.publisher"
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

# ------------------------------------------- make dead-lettering actually work
# Attaching `--dead-letter-topic` above is NOT enough. Pub/Sub forwards a
# dead-lettered message as its own service agent, which needs
# roles/pubsub.publisher on the dead-letter TOPIC and roles/pubsub.subscriber
# on each SUBSCRIPTION doing the forwarding. Neither is implied by anything
# else granted here. Confirmed live 2026-08-26: every topic and subscription
# policy in the project was empty (`etag: ACAB`), and roles/pubsub.serviceAgent
# -- which the agent DOES hold at project level -- contains ten permissions,
# all of them iam/resourcemanager/serviceusage, and no `pubsub.topics.publish`
# whatsoever (`gcloud iam roles describe roles/pubsub.serviceAgent`).
#
# So all five subscriptions carried a dead-letter policy that could never fire:
# a poison message would retry to max-delivery-attempts, then sit until the
# 1-day retention silently dropped it, and the `everyfront-dead-letter-nonempty`
# alert further down -- which exists and is enabled -- could never trigger,
# because nothing could ever reach the topic it watches. Same class of defect
# as the five PULL subscriptions with no subscriber: infrastructure that is
# present, correct-looking, and structurally incapable of doing its job.
#
# Not fatal on failure, unlike the Gmail grant above: a project without these
# still processes every healthy message, it just loses the safety net. Warn
# loudly rather than halting a bootstrap over it.
log "Dead-letter delivery permissions"
if gcloud pubsub topics get-iam-policy dead-letter \
     --format='value(bindings.members)' 2>/dev/null | grep -q "$PUBSUB_AGENT"; then
  skip "pubsub service agent publisher on dead-letter"
elif gcloud pubsub topics add-iam-policy-binding dead-letter \
       --member="serviceAccount:${PUBSUB_AGENT}" \
       --role="roles/pubsub.publisher" >/dev/null 2>&1; then
  ok "pubsub service agent -> publisher on dead-letter"
else
  warn "could not grant publisher on dead-letter -- dead-lettering will silently drop messages"
fi
while IFS='|' read -r topic sub; do
  [ -z "$topic" ] && continue
  if gcloud pubsub subscriptions get-iam-policy "$sub" \
       --format='value(bindings.members)' 2>/dev/null | grep -q "$PUBSUB_AGENT"; then
    skip "  pubsub service agent subscriber on $sub"
  elif gcloud pubsub subscriptions add-iam-policy-binding "$sub" \
         --member="serviceAccount:${PUBSUB_AGENT}" \
         --role="roles/pubsub.subscriber" >/dev/null 2>&1; then
    ok "  pubsub service agent -> subscriber on $sub"
  else
    warn "could not grant subscriber on $sub -- its dead-letter policy cannot fire"
  fi
done <<< "$SUB_RECORDS"

# ---------------------------------------------------------------- service accounts
log "Service accounts (least privilege)"
# macOS ships bash 3.2, which has no associative arrays. Parallel
# "name|roles" records keep this runnable on the dev machine AND on Cloud Shell.
#
# roles/secretmanager.secretAccessor is deliberately NOT in this list. It used
# to be, at PROJECT scope, for ef-intake and ef-agent -- which meant ef-intake,
# whose only legitimate need is the three google-oauth-* values, could read
# every vendor key in the project (lob-api-key, phaxio-api-secret, ...) the
# moment those secrets existed. Secrets are now bound one at a time, per
# consumer, in the section below. WO1 says least privilege and this repo is
# public with a README that makes claims about its own security posture.
SA_RECORDS="
ef-intake|roles/pubsub.publisher roles/storage.objectAdmin
ef-agent|roles/pubsub.publisher roles/pubsub.subscriber roles/datastore.user roles/storage.objectAdmin roles/aiplatform.user
ef-api|roles/datastore.user roles/pubsub.publisher roles/storage.objectViewer
ef-web|
"
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

# ---------------------------------------------------------------- dead-letter alerting
# §2.3 requires handlers tolerate redelivery, but a poison message that
# exhausts max-delivery-attempts (5, set above) and lands in `dead-letter`
# must not then vanish silently. `ef-dead-letter` gives it somewhere to land;
# without this alert nothing ever looks at that somewhere -- confirmed live
# 2026-08-25: `ef-dead-letter` sat with zero subscribers reading it and zero
# monitoring policies of any kind on the project, so a dead-lettered message
# would sit unnoticed until its 7-day retention silently dropped it. This is
# the "something would notice" half of WO4/WO6 task 4.
log "Dead-letter alerting"
gcloud components install alpha --quiet >/dev/null 2>&1 || true
if ! gcloud alpha monitoring channels list --format='value(name)' >/dev/null 2>&1; then
  printf '    \033[33mwarn\033[0m gcloud alpha monitoring is unavailable (managed install?) -- set up a dead-letter alert by hand\n'
else
  ALERT_EMAIL="${ALERT_EMAIL:-$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)}"
  CHANNEL_NAME="$(gcloud alpha monitoring channels list \
    --filter='displayName="everyfront-ops-email"' --format='value(name)' 2>/dev/null | head -1)"
  if [[ -n "$CHANNEL_NAME" ]]; then
    skip "notification channel everyfront-ops-email"
  else
    _channel_json="$(mktemp)"
    printf '{"type":"email","displayName":"everyfront-ops-email","labels":{"email_address":"%s"}}' \
      "$ALERT_EMAIL" > "$_channel_json"
    CHANNEL_NAME="$(gcloud alpha monitoring channels create \
      --channel-content-from-file="$_channel_json" --format='value(name)' 2>/dev/null || true)"
    rm -f "$_channel_json"
    if [[ -n "$CHANNEL_NAME" ]]; then
      ok "notification channel -> $ALERT_EMAIL"
    else
      printf '    \033[33mwarn\033[0m could not create a notification channel (needs monitoring.notificationChannels.create) -- dead-letter alert skipped\n'
    fi
  fi

  if gcloud alpha monitoring policies list \
       --filter='displayName="everyfront-dead-letter-nonempty"' --format='value(name)' 2>/dev/null | grep -q .; then
    skip "alert policy everyfront-dead-letter-nonempty"
  elif [[ -n "$CHANNEL_NAME" ]]; then
    _policy_json="$(mktemp)"
    cat > "$_policy_json" <<POLICY_EOF
{
  "displayName": "everyfront-dead-letter-nonempty",
  "documentation": {
    "content": "A message landed in the \`dead-letter\` Pub/Sub topic (ef-dead-letter subscription) -- a handler failed on it 5 times (max-delivery-attempts) and Pub/Sub gave up redelivering. Investigate: \`gcloud pubsub subscriptions pull ef-dead-letter --auto-ack --limit=10\`.",
    "mimeType": "text/markdown"
  },
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "ef-dead-letter has undelivered messages",
      "conditionThreshold": {
        "filter": "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"ef-dead-letter\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0,
        "duration": "0s",
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MAX"}]
      }
    }
  ],
  "alertStrategy": {"autoClose": "604800s"}
}
POLICY_EOF
    if gcloud alpha monitoring policies create --policy-from-file="$_policy_json" \
         --notification-channels="$CHANNEL_NAME" >/dev/null 2>&1; then
      ok "alert: ef-dead-letter non-empty -> $ALERT_EMAIL"
    else
      printf '    \033[33mwarn\033[0m could not create the dead-letter alert policy (needs monitoring.alertPolicies.create)\n'
    fi
    rm -f "$_policy_json"
  fi
fi

log "Setup complete for $PROJECT_ID"
cat <<EOF

  next:  ./infra/deploy.sh all
  check: gcloud pubsub topics list
         gcloud storage ls
EOF
