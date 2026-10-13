resource "google_container_cluster" "autopilot" {
  name     = "${var.name}-autopilot"
  location = var.region

  enable_autopilot    = true
  deletion_protection = var.deletion_protection

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.nodes.id

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Private nodes; the control plane keeps a public endpoint restricted to
  # authorized_cidrs so kubectl works without a bastion.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "operator"
      }
    }
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  depends_on = [google_project_iam_member.nodes, google_compute_router_nat.nat]
}
