variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for the regional Autopilot cluster."
  type        = string
  default     = "us-west1"
}

variable "name" {
  description = "Name prefix for every resource in this stack."
  type        = string
  default     = "healer"
}

variable "authorized_cidrs" {
  description = "CIDRs allowed to reach the public control-plane endpoint (e.g. your IP as x.x.x.x/32)."
  type        = list(string)
}

variable "nodes_cidr" {
  description = "Primary subnet range for nodes."
  type        = string
  default     = "10.30.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pods."
  type        = string
  default     = "10.32.0.0/14"
}

variable "services_cidr" {
  description = "Secondary range for services."
  type        = string
  default     = "10.36.0.0/20"
}

variable "deletion_protection" {
  description = "Protect the cluster from terraform destroy. Off for a lab."
  type        = bool
  default     = false
}
