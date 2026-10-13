# Dedicated node identity instead of the Compute Engine default SA (which has Editor).
resource "google_service_account" "nodes" {
  account_id   = "${var.name}-gke-nodes"
  display_name = "GKE Autopilot nodes (${var.name})"
}

resource "google_project_iam_member" "nodes" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}
