#!/usr/bin/env bash
# Build the agent image for linux/amd64 (both clusters are amd64; needed on Apple Silicon) and push to GHCR.
# Usage: echo $GITHUB_PAT | docker login ghcr.io -u birmingham --password-stdin; scripts/build-push.sh 0.1.0
set -euo pipefail
TAG="${1:?usage: $0 <tag>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=ghcr.io/birmingham/k8s-self-healing-agent
docker buildx build --platform linux/amd64 -t "$IMAGE:$TAG" --push "$ROOT/agent"
echo "pushed $IMAGE:$TAG (make the GHCR package public once so the clusters can pull without a secret)"
