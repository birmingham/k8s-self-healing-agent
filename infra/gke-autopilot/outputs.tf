output "cluster_name" {
  value = google_container_cluster.autopilot.name
}

output "location" {
  value = google_container_cluster.autopilot.location
}

output "connect" {
  description = "Fetch credentials and name the context healer-gke."
  value       = "scripts/connect-gke.sh ${var.project_id} ${var.region} ${google_container_cluster.autopilot.name}"
}
