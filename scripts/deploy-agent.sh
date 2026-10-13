#!/usr/bin/env bash
# Deploy the agent to one cluster: secret from Secret Manager, then kustomize overlay.
# Usage: scripts/deploy-agent.sh <healer-gke|healer-rke2> <project-id>
set -euo pipefail
CTX="${1:?usage: $0 <healer-gke|healer-rke2> <project-id>}"
PROJECT="${2:?usage: $0 <context> <project-id>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

case "$CTX" in
  healer-gke)  OVERLAY=gke-autopilot ;;
  healer-rke2) OVERLAY=rke2 ;;
  *) echo "unknown context $CTX" >&2; exit 64 ;;
esac
K=(kubectl --context "$CTX")

"${K[@]}" create namespace healer-system --dry-run=client -o yaml | "${K[@]}" apply -f -

# Pipe the key straight from Secret Manager into the Secret: never on disk, never in argv.
gcloud secrets versions access latest --secret=anthropic-api-key --project "$PROJECT" \
  | "${K[@]}" -n healer-system create secret generic healer-secrets \
      --from-file=ANTHROPIC_API_KEY=/dev/stdin --dry-run=client -o yaml \
  | "${K[@]}" apply -f -

"${K[@]}" apply -k "$ROOT/deploy/overlays/$OVERLAY"
"${K[@]}" -n healer-system rollout status deploy/healer --timeout=300s
