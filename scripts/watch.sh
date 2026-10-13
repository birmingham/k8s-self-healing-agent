#!/usr/bin/env bash
# Follow the agent's audit log in a readable form. Needs jq.
# Usage: scripts/watch.sh [context]
set -euo pipefail
K=(kubectl)
[[ -n "${1:-}" ]] && K+=(--context "$1")
"${K[@]}" -n healer-system logs deploy/healer -f --since=1h | jq -r --unbuffered '
  [.time[11:19], .severity, .event, (.incident // ""), (.outcome // ""), (.action // ""),
   (.reason // .root_cause // .approve_with // "")] | map(tostring) | join("  ")'
