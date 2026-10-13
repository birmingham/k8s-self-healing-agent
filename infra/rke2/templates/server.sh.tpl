#!/usr/bin/env bash
# RKE2 server bootstrap. Rendered by Terraform templatefile(): only $${...}
# and %%{...} are template syntax; plain $VAR and $(cmd) are bash.
set -euo pipefail
exec >>/var/log/rke2-bootstrap.log 2>&1
export HOME=/root   # GCE startup scripts run without HOME; RKE2's Helm tooling needs it

MARKER=/var/lib/rancher/rke2/.bootstrap-done
if [[ -f "$MARKER" ]]; then
  echo "$(date -Is) already bootstrapped; ensuring rke2-server is running"
  systemctl start rke2-server.service
  exit 0
fi
echo "$(date -Is) bootstrapping RKE2 server on ${node_ip}"

mkdir -p /etc/rancher/rke2 /var/lib/rancher/rke2/server/manifests

cat >/etc/rancher/rke2/config.yaml <<CFG
token: ${token}
node-ip: ${node_ip}
write-kubeconfig-mode: "0600"
cni: canal
tls-san:
  - 127.0.0.1
  - localhost
%{ for ip in tls_sans ~}
  - ${ip}
%{ endfor ~}
CFG
chmod 600 /etc/rancher/rke2/config.yaml

# GCP's VPC MTU is 1460, not 1500. Canal's VXLAN adds 50 bytes, so pods must
# use 1410 or large cross-node packets silently stall while pings still work.
cat >/var/lib/rancher/rke2/server/manifests/rke2-canal-config.yaml <<'MTU'
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-canal
  namespace: kube-system
spec:
  valuesContent: |-
    calico:
      vethuMTU: ${veth_mtu}
MTU

curl -sfL https://get.rke2.io -o /tmp/install-rke2.sh
INSTALL_RKE2_CHANNEL="${channel}" INSTALL_RKE2_VERSION="${version}" sh /tmp/install-rke2.sh
systemctl enable --now rke2-server.service

ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
until kubectl get --raw /readyz >/dev/null 2>&1; do
  echo "$(date -Is) waiting for API server"; sleep 10
done
touch "$MARKER"
echo "$(date -Is) RKE2 server ready"
