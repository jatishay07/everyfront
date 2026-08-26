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

# Vertex serves Gemini 3.x ONLY from the `global` endpoint. Probed 2026-08-25:
# us-central1 returns 404 for gemini-3.7-flash / 3.5-flash and serves nothing
# newer than 2.5-flash -- which is BELOW the §1.3 "Gemini 3.5 or newer" bar and
# would silently disqualify the submission. Cloud Run stays regional; only the
# model endpoint is global.
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"

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

# Which packages/ each service needs on its PYTHONPATH. `gcloud run deploy
# --source=services/<name>` uploads ONLY that directory, so a service importing
# packages/rules builds and tests fine locally and then dies at runtime in Cloud
# Run with ModuleNotFoundError. RELAY hit this; the deployed agent-core survived
# only because it happened to define its tool inline instead of importing.
#
# Duplicating the packages into each service is not an option for the rules
# engine -- §2.1 makes it the single source of truth for the law, and two copies
# drift. So we stage a build context containing the service plus what it needs.
#
# LEDGER WO6: agent-core previously omitted `datapipes` here, so its own
# `mrf_cache`/`ncci_cache` modules were unconditionally unavailable in the
# deployed container -- confirmed live: `demo/inject_bill` reported
# `cash_price_source: "skipped -- packages/datapipes not importable"` for
# every case, and the NCCI PTP/MUE checks had no table to query even once
# wired. `datapipes` carries a bundled, offline NCCI snapshot (no network at
# runtime) plus the live MRF fetcher used as a fallback -- see those two
# modules' docstrings in services/agent-core/agent_core/.
pkgs_for() {
  case "$1" in
    intake)     echo "delivery" ;;
    agent-core) echo "rules delivery datapipes" ;;
    api)        echo "rules" ;;
    *)          echo "" ;;
  esac
}

# Extra top-level directories a service needs bundled into its image. The API
# serves PROOF's fixture corpus from `POST /demo/inject_bill`, which drives the
# whole demo -- so the fixtures have to be IN the container, not merely on the
# dev machine.
data_for() {
  case "$1" in
    api) echo "fixtures" ;;
    *)   echo "" ;;
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

  # Stage a build context: the service at the root, plus each needed package
  # under packages/ so PYTHONPATH resolves the same way it does locally.
  pkgs="$(pkgs_for "$svc")"
  data="$(data_for "$svc")"
  ctx="$dir"
  pythonpath=""
  if [ -n "$pkgs" ] || [ -n "$data" ]; then
    ctx="$(mktemp -d)"
    trap 'rm -rf "$ctx"' RETURN 2>/dev/null || true
    cp -R "$dir"/. "$ctx"/
    mkdir -p "$ctx/packages"
    for pkg in $pkgs; do
      if [ -d "packages/$pkg/$pkg" ]; then
        cp -R "packages/$pkg/$pkg" "$ctx/packages/$pkg"
      else
        die "service '$svc' needs packages/$pkg but it does not exist"
      fi
    done
    for d in $(data_for "$svc"); do
      if [ -d "$d" ]; then
        cp -R "$d" "$ctx/$d"
        ok "  bundled $d/"
      else
        die "service '$svc' needs $d/ but it does not exist"
      fi
    done
    # Bytecode from the host is wrong for the container and only bloats the
    # upload; the image rebuilds it.
    find "$ctx" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    find "$ctx" -name '*.pyc' -delete 2>/dev/null || true
    pythonpath="/app/packages"
    ok "staged $svc + packages: $pkgs"
  fi

  # Service-to-service wiring. services/api calls agent-core over HTTP for the
  # two synchronous actions (inject_bill, approve_filing) and defaults to
  # localhost, which is correct locally and useless in Cloud Run. Resolve the
  # real URL at deploy time rather than hardcoding it anywhere.
  extra_env=""
  if [ "$svc" = "web" ]; then
    # The dashboard proxies to the API server-side (services/api sends no CORS
    # headers, so a direct browser fetch from the web origin is blocked). The
    # URL is read at REQUEST time, not baked at build time, so the API can be
    # repointed with `gcloud run services update` and no rebuild.
    api_url="$(gcloud run services describe ef-api --region="$REGION" \
      --format='value(status.url)' 2>/dev/null || true)"
    if [ -n "$api_url" ]; then
      extra_env=",API_BASE_URL=${api_url}"
      ok "wired API_BASE_URL -> $api_url"
    else
      printf '    \033[33mwarn\033[0m ef-api not deployed yet; web will fall back to mock data\n'
    fi
  fi
  if [ "$svc" = "api" ]; then
    agent_url="$(gcloud run services describe ef-agent-core --region="$REGION" \
      --format='value(status.url)' 2>/dev/null || true)"
    if [ -n "$agent_url" ]; then
      extra_env=",AGENT_CORE_URL=${agent_url}"
      ok "wired AGENT_CORE_URL -> $agent_url"
    else
      printf '    \033[33mwarn\033[0m ef-agent-core not deployed yet; api will fall back to localhost\n'
    fi
  fi

  log "Deploying $svc from $dir"
  gcloud run deploy "ef-${svc}" \
    --source="$ctx" \
    --region="$REGION" \
    --service-account="${sa}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --min-instances=0 \
    --max-instances=4 \
    --memory=1Gi \
    --timeout=300 \
    --allow-unauthenticated \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=TRUE${pythonpath:+,PYTHONPATH=$pythonpath}${extra_env}" \
    --quiet >/dev/null
  url="$(gcloud run services describe "ef-${svc}" --region="$REGION" --format='value(status.url)')"
  ok "$svc -> $url"

  # Point this service's Pub/Sub subscriptions at it.
  #
  # setup.sh creates these as PULL subscriptions and its own comment promised
  # deploy.sh would convert them to push "once the Cloud Run URLs exist". That
  # conversion was never written, so every subscription sat with no subscriber:
  # approve_filing published `filing.requested` into a queue nobody read, the
  # front flipped to "filing", and no filing was ever produced. Nothing errored.
  #
  # The demo path worked only because /demo/inject_bill calls agent-core
  # SYNCHRONOUSLY over HTTP, bypassing the event backbone entirely -- so the
  # one required §1.3 service that was provisioned and named correctly was also
  # completely inert, and looked fine from every angle except an actual filing.
  wire_push() {
    _sub="$1"; _path="$2"
    gcloud pubsub subscriptions describe "$_sub" >/dev/null 2>&1 || return 0
    gcloud pubsub subscriptions modify-push-config "$_sub" \
      --push-endpoint="${url}${_path}" \
      --push-auth-service-account="${sa}@${PROJECT_ID}.iam.gserviceaccount.com" \
      >/dev/null 2>&1 \
      && ok "  $_sub -> ${_path}" \
      || printf '    \033[33mwarn\033[0m could not wire %s -- it stays PULL and nothing will consume it\n' "$_sub"
  }
  case "$svc" in
    agent-core)
      wire_push ef-document-added   /pubsub/document-added
      wire_push ef-filing-requested /pubsub/filing-requested
      ;;
    intake)
      wire_push ef-intake-email     /pubsub/gmail
      ;;
  esac
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
