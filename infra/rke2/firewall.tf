locals {
  node_tag = "${var.name}-rke2"
}

# IAP's TCP forwarding range: SSH and the Kubernetes API, nothing from the internet.
resource "google_compute_firewall" "iap" {
  name          = "${var.name}-rke2-allow-iap"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.node_tag]

  allow {
    protocol = "tcp"
    ports    = ["22", "6443"]
  }
}

# Node-to-node: supervisor 9345, API 6443, kubelet 10250, etcd 2379-2380,
# Canal VXLAN 8472/udp, NodePorts. Scoped by tag, not by subnet range.
resource "google_compute_firewall" "internal" {
  name        = "${var.name}-rke2-allow-internal"
  network     = google_compute_network.vpc.id
  direction   = "INGRESS"
  source_tags = [local.node_tag]
  target_tags = [local.node_tag]

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }
}
