#!/usr/bin/env bash
# Run a failure scenario against the current kube context (or --context X).
# Usage: scenarios/run.sh [--context CTX] <1|2|3|4|reset>
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
NS=healer-demo
K=(kubectl)
if [[ "${1:-}" == "--context" ]]; then
  K+=(--context "$2"); shift 2
fi
k() { "${K[@]}" -n "$NS" "$@"; }

fresh() {  # $1 deployment, $2 manifest: delete then re-create so history starts clean
  k delete deploy "$1" --ignore-not-found --wait=true
  "${K[@]}" apply -f "$DIR/$2"
}

case "${1:-}" in
  1|bad-rollout)
    fresh web 01-bad-rollout.yaml
    k rollout status deploy/web --timeout=300s
    echo ">> shipping a bad image tag (revision 2)"
    k set image deploy/web web=nginxinc/nginx-unprivileged:1.27-alpinee
    echo ">> expect: ImagePullBackOff -> agent rolls back to revision 1 automatically"
    ;;
  2|oom)
    fresh cruncher 02-oom.yaml
    echo ">> expect: OOMKilled -> agent proposes 256Mi -> 512Mi -> run scripts/approve.sh cruncher"
    ;;
  3|corrupt-cache)
    fresh cache-worker 03-corrupt-cache.yaml
    k rollout status deploy/cache-worker --timeout=300s
    echo ">> corrupting the pod-local cache"
    k exec deploy/cache-worker -- touch /cache/state.db.corrupt
    echo ">> expect: CrashLoopBackOff -> agent deletes the pod -> fresh emptyDir -> healthy"
    ;;
  4|missing-config)
    fresh api 04-missing-config.yaml
    echo ">> expect: CrashLoopBackOff -> agent escalates (no safe automated fix) and changes nothing"
    ;;
  reset)
    k delete deploy -l healer.birmingham.github.io/scenario --ignore-not-found
    ;;
  *)
    echo "usage: $0 [--context CTX] <1|bad-rollout|2|oom|3|corrupt-cache|4|missing-config|reset>" >&2
    exit 64
    ;;
esac
