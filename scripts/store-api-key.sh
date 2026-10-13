#!/usr/bin/env bash
# Store the Anthropic API key in Secret Manager (prompted, never echoed or put in shell history).
# Usage: scripts/store-api-key.sh <project-id>
set -euo pipefail
PROJECT="${1:?usage: $0 <project-id>}"
SECRET=anthropic-api-key

gcloud services enable secretmanager.googleapis.com --project "$PROJECT"
read -rsp "Anthropic API key: " KEY; echo
[[ -n "$KEY" ]] || { echo "empty key" >&2; exit 1; }

if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy=automatic
fi
printf '%s' "$KEY" | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=-
unset KEY
echo "stored as secret '$SECRET' in $PROJECT"
