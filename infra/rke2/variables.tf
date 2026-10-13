variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "zone" {
  type    = string
  default = "us-west1-a"
}

variable "name" {
  description = "Name prefix for every resource in this stack."
  type        = string
  default     = "healer"
}

variable "subnet_cidr" {
  type    = string
  default = "10.40.0.0/24"
}

variable "agent_count" {
  description = "Number of RKE2 agent (worker) nodes."
  type        = number
  default     = 2
}

variable "machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "image" {
  type    = string
  default = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
}

variable "disk_gb" {
  type    = number
  default = 50
}

variable "rke2_channel" {
  description = "RKE2 release channel, used when rke2_version is empty."
  type        = string
  default     = "stable"
}

variable "rke2_version" {
  description = "Pin an exact RKE2 version (e.g. from github.com/rancher/rke2/releases). Empty = latest in channel."
  type        = string
  default     = ""
}

variable "veth_mtu" {
  description = "Canal pod MTU. GCP VPC MTU is 1460; VXLAN adds 50 bytes, so 1410."
  type        = number
  default     = 1410
}
