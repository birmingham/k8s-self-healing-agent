# Bucket and prefix come from backend.hcl (gitignored):
#   terraform init -backend-config=backend.hcl
terraform {
  backend "gcs" {}
}
