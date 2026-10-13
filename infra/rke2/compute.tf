resource "random_password" "rke2_token" {
  length  = 48
  special = false
}

# Static internal IPs reserved before boot, so the server's tls-san list and
# every agent's join address are known at render time.
resource "google_compute_address" "server" {
  name         = "${var.name}-rke2-server-0"
  region       = var.region
  subnetwork   = google_compute_subnetwork.nodes.id
  address_type = "INTERNAL"
  address      = cidrhost(var.subnet_cidr, 10)
}

resource "google_compute_address" "agents" {
  count        = var.agent_count
  name         = "${var.name}-rke2-agent-${count.index}"
  region       = var.region
  subnetwork   = google_compute_subnetwork.nodes.id
  address_type = "INTERNAL"
  address      = cidrhost(var.subnet_cidr, 11 + count.index)
}

locals {
  common_metadata = {
    enable-oslogin = "TRUE"
  }
}

resource "google_compute_instance" "server" {
  name         = "${var.name}-rke2-server-0"
  zone         = var.zone
  machine_type = var.machine_type
  tags         = [local.node_tag]

  boot_disk {
    initialize_params {
      image = var.image
      size  = var.disk_gb
      type  = "pd-balanced"
    }
  }

  # No access_config block: no external IP. Egress via Cloud NAT, ingress via IAP.
  network_interface {
    subnetwork = google_compute_subnetwork.nodes.id
    network_ip = google_compute_address.server.address
  }

  service_account {
    email  = google_service_account.nodes.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = local.common_metadata
  metadata_startup_script = templatefile("${path.module}/templates/server.sh.tpl", {
    token    = random_password.rke2_token.result
    node_ip  = google_compute_address.server.address
    tls_sans = concat([google_compute_address.server.address], google_compute_address.agents[*].address)
    channel  = var.rke2_channel
    version  = var.rke2_version
    veth_mtu = var.veth_mtu
  })

  depends_on = [google_compute_router_nat.nat, google_project_iam_member.nodes]
}

resource "google_compute_instance" "agents" {
  count        = var.agent_count
  name         = "${var.name}-rke2-agent-${count.index}"
  zone         = var.zone
  machine_type = var.machine_type
  tags         = [local.node_tag]

  boot_disk {
    initialize_params {
      image = var.image
      size  = var.disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.nodes.id
    network_ip = google_compute_address.agents[count.index].address
  }

  service_account {
    email  = google_service_account.nodes.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = local.common_metadata
  metadata_startup_script = templatefile("${path.module}/templates/agent.sh.tpl", {
    token     = random_password.rke2_token.result
    node_ip   = google_compute_address.agents[count.index].address
    server_ip = google_compute_address.server.address
    channel   = var.rke2_channel
    version   = var.rke2_version
  })

  depends_on = [google_compute_router_nat.nat, google_project_iam_member.nodes]
}
