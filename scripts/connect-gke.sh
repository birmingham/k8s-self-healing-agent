#!/usr/bin/env bash
# Fetch credentials for the Autopilot cluster and name the context healer-gke.
# Usage: scripts/connect-gke.sh <project-id> [region] [cluster-name]
set -euo pipefail
PROJECT="${1:?usage: $0 <project-id> [region] [cluster]}"
REGION="${2:-us-west1}"
CLUSTER="${3:-healer-autopilot}"

gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT"
kubectl config delete-context healer-gke >/dev/null 2>&1 || true
kubectl config rename-context "gke_${PROJECT}_${REGION}_${CLUSTER}" healer-gke
kubectl --context healer-gke get nodes -o wide || true   # Autopilot may show none until pods schedule
