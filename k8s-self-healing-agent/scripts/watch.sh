#!/usr/bin/env bash
# Follow the agent's audit log in a readable form. Needs jq.
# Non-JSON lines (library output, tracebacks) are printed as-is instead of breaking the stream.
# Usage: scripts/watch.sh [context]
set -euo pipefail
K=(kubectl)
[[ -n "${1:-}" ]] && K+=(--context "$1")
"${K[@]}" -n healer-system logs deploy/healer -f --since=1h | jq -rR --unbuffered '
  . as $line
  | (try fromjson catch null) as $j
  | if ($j | type) == "object" then
      [($j.time // "")[11:19], ($j.severity // ""), ($j.event // ""), ($j.incident // ""),
       ($j.outcome // ""), ($j.action // ""),
       ($j.reason // $j.root_cause // $j.approve_with // "")] | map(tostring) | join("  ")
    else "   [raw] " + $line end'
