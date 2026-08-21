#!/usr/bin/env bash
# ATLAS (persona 1), work order 2 -- build and deploy a service to Cloud Run.
#
#   usage:  PROJECT_ID=... ./infra/deploy.sh all
#           PROJECT_ID=... ./infra/deploy.sh agent-core
#
# Scale-to-zero on every service (§4 persona 1 WO4). min-instances=0 is the
# default but is set explicitly so nobody "optimizes" cold starts by pinning a
# warm instance and quietly burning the §6 budget overnight.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
TARGET="${1:-all}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# macOS ships bash 3.2 -- no associative arrays. Plain functions instead.
SERVICES="intake agent-core api web"

src_for() {
  case "$1" in
    intake)     echo "services/intake" ;;
    agent-core) echo "services/agent-core" ;;
    api)        echo "services/api" ;;
    web)        echo "web" ;;
    *)          echo "" ;;
  esac
}

sa_for() {
  case "$1" in
    intake)     echo "ef-intake" ;;
    agent-core) echo "ef-agent" ;;
    api)        echo "ef-api" ;;
    web)        echo "ef-web" ;;
    *)          echo "" ;;
  esac
}

deploy_one() {
  svc="$1"
  dir="$(src_for "$svc")"
  sa="$(sa_for "$svc")"
  [ -n "$dir" ] || die "unknown service '$svc' (have: $SERVICES all)"
  [ -d "$dir" ] || die "$dir does not exist"
  if [ ! -f "$dir/Dockerfile" ]; then
    printf '    \033[33mskip\033[0m %s (no Dockerfile yet)\n' "$svc"
    return 0
  fi

  log "Deploying $svc from $dir"
  gcloud run deploy "ef-${svc}" \
    --source="$dir" \
    --region="$REGION" \
    --service-account="${sa}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --min-instances=0 \
    --max-instances=4 \
    --memory=1Gi \
    --timeout=300 \
    --allow-unauthenticated \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION}" \
    --quiet >/dev/null
  url="$(gcloud run services describe "ef-${svc}" --region="$REGION" --format='value(status.url)')"
  ok "$svc -> $url"
}

# Validate before touching the caller's gcloud config -- a bad argument should
# not leave their active project pointing somewhere else.
if [ "$TARGET" != "all" ] && [ -z "$(src_for "$TARGET")" ]; then
  die "unknown service '$TARGET' (have: $SERVICES all)"
fi

gcloud config set project "$PROJECT_ID" >/dev/null

if [ "$TARGET" = "all" ]; then
  for svc in $SERVICES; do deploy_one "$svc"; done
else
  deploy_one "$TARGET"
fi

log "Deployed. Public URLs:"
gcloud run services list --region="$REGION" --format='table(metadata.name,status.url)'
