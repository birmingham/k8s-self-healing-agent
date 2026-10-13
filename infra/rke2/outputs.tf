output "server_name" {
  value = google_compute_instance.server.name
}

output "node_ips" {
  value = concat([google_compute_address.server.address], google_compute_address.agents[*].address)
}

output "connect" {
  description = "Pull the kubeconfig over IAP and open an API tunnel on localhost:16443."
  value       = "scripts/connect-rke2.sh ${var.project_id} ${var.zone} ${google_compute_instance.server.name}"
}

output "bootstrap_log" {
  value = "gcloud compute ssh ${google_compute_instance.server.name} --zone ${var.zone} --tunnel-through-iap --command 'sudo tail -f /var/log/rke2-bootstrap.log'"
}
