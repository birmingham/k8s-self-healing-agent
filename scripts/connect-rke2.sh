#!/usr/bin/env bash
# Pull the RKE2 kubeconfig over IAP, rename it to context healer-rke2, and open
# an IAP tunnel to the API server on localhost:16443. Leave it running.
# Usage: scripts/connect-rke2.sh <project-id> [zone] [server-vm]
set -euo pipefail
PROJECT="${1:?usage: $0 <project-id> [zone] [server-vm]  (project ID is empty: export PROJECT=...)}"
ZONE="${2:-us-west1-a}"
VM="${3:-healer-rke2-server-0}"
PORT=16443
OUT="$HOME/.kube/healer-rke2-kubeconfig.yaml"

mkdir -p "$HOME/.kube"
gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap \
  --command 'sudo cat /etc/rancher/rke2/rke2.yaml' > "$OUT.tmp"

# Portable BRE (works with macOS sed): rename "default" -> healer-rke2, point at the tunnel.
# [- ]* matters: the users entry is written "- name: default" (list item), the
# cluster and context names are "  name: default" (indented).
sed -e 's/^\([- ]*name:\) default$/\1 healer-rke2/' \
    -e 's/^\( *cluster:\) default$/\1 healer-rke2/' \
    -e 's/^\( *user:\) default$/\1 healer-rke2/' \
    -e 's/^current-context: default$/current-context: healer-rke2/' \
    -e "s#server: https://127.0.0.1:6443#server: https://127.0.0.1:${PORT}#" \
    "$OUT.tmp" > "$OUT"
rm -f "$OUT.tmp"
chmod 600 "$OUT"

# Every reference must resolve, or kubectl silently falls back to a password prompt.
if grep -q ': default$' "$OUT" || [[ "$(grep -c 'healer-rke2$' "$OUT")" -lt 6 ]]; then
  echo "kubeconfig rename incomplete; inspect $OUT" >&2
  exit 1
fi

cat <<MSG
Kubeconfig written to $OUT
In another terminal run:
  export KUBECONFIG="\$HOME/.kube/config:$OUT"
  kubectl --context healer-rke2 get nodes

Opening IAP tunnel to $VM:6443 on localhost:$PORT (Ctrl-C to close)...
MSG
exec gcloud compute start-iap-tunnel "$VM" 6443 --local-host-port="localhost:${PORT}" \
  --zone "$ZONE" --project "$PROJECT"
