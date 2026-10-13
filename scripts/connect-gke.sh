#!/usr/bin/env bash
# Fetch credentials for the Autopilot cluster and name the context healer-gke.
# Waits for the cluster to reach RUNNING first, so it is safe to start while
# `terraform apply` is still creating it.
# Usage: scripts/connect-gke.sh <project-id> [region] [cluster-name]
set -euo pipefail
PROJECT="${1:?usage: $0 <project-id> [region] [cluster]  (project ID is empty: export PROJECT=...)}"
REGION="${2:-us-west1}"
CLUSTER="${3:-healer-autopilot}"
TIMEOUT_MIN=25

for ((i = 0; i < TIMEOUT_MIN * 2; i++)); do
  STATUS="$(gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT" \
    --format='value(status)' 2>/dev/null || true)"
  [[ "$STATUS" == "RUNNING" ]] && break
  echo "$(date +%H:%M:%S) cluster status: ${STATUS:-not found yet}; waiting for RUNNING (wait for terraform apply to finish)"
  sleep 30
done
if [[ "$STATUS" != "RUNNING" ]]; then
  echo "cluster $CLUSTER not RUNNING after $TIMEOUT_MIN minutes; check terraform apply" >&2
  exit 1
fi

gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT"
kubectl config delete-context healer-gke >/dev/null 2>&1 || true
kubectl config rename-context "gke_${PROJECT}_${REGION}_${CLUSTER}" healer-gke
kubectl --context healer-gke get ns
