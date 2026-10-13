#!/usr/bin/env bash
# Show the agent's pending proposal for a deployment and approve it.
# Usage: scripts/approve.sh <deployment> [context]
set -euo pipefail
DEP="${1:?usage: $0 <deployment> [context]}"
K=(kubectl -n healer-demo)
[[ -n "${2:-}" ]] && K+=(--context "$2")

PROP="$("${K[@]}" get deploy "$DEP" -o "jsonpath={.metadata.annotations['healer\.birmingham\.github\.io/proposal']}")"
[[ -n "$PROP" ]] || { echo "no pending proposal on $DEP"; exit 1; }
echo "$PROP" | jq .
ID="$(echo "$PROP" | jq -r .id)"
read -rp "Approve proposal $ID? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "not approved"; exit 0; }
"${K[@]}" annotate deploy "$DEP" "healer.birmingham.github.io/approve=$ID" --overwrite
echo "approved; the agent re-validates and applies it on its next loop (<= 30s)."
