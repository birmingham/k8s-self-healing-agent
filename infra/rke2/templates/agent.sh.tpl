#!/usr/bin/env bash
# RKE2 agent bootstrap. Rendered by Terraform templatefile().
set -euo pipefail
exec >>/var/log/rke2-bootstrap.log 2>&1
export HOME=/root

MARKER=/var/lib/rancher/rke2/.bootstrap-done
if [[ -f "$MARKER" ]]; then
  systemctl start rke2-agent.service
  exit 0
fi
echo "$(date -Is) bootstrapping RKE2 agent on ${node_ip}"

mkdir -p /etc/rancher/rke2
cat >/etc/rancher/rke2/config.yaml <<CFG
server: https://${server_ip}:9345
token: ${token}
node-ip: ${node_ip}
CFG
chmod 600 /etc/rancher/rke2/config.yaml

# VMs boot in parallel; wait for the server's supervisor port before joining.
until (exec 3<>/dev/tcp/${server_ip}/9345) 2>/dev/null; do
  echo "$(date -Is) waiting for server ${server_ip}:9345"; sleep 10
done

curl -sfL https://get.rke2.io -o /tmp/install-rke2.sh
INSTALL_RKE2_TYPE=agent INSTALL_RKE2_CHANNEL="${channel}" INSTALL_RKE2_VERSION="${version}" sh /tmp/install-rke2.sh
systemctl enable --now rke2-agent.service
mkdir -p "$(dirname "$MARKER")" && touch "$MARKER"
echo "$(date -Is) RKE2 agent started"
