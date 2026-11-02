variable "pool_project_id" {
  description = "Project that hosts tenant runner VMs (ADR-020). Created by hand on its own billing account."
  type        = string
  default     = "runnerforge-pool"

  validation {
    condition     = var.pool_project_id != "runnerforge"
    error_message = "The pool module must never target the control project."
  }
}

locals {
  region = "europe-west4"

  # Every non-public range GCP accepts for a subnet, plus link-local
  # (https://cloud.google.com/vpc/docs/subnets#valid-ranges). Tenant VMs may
  # reach none of them, in either direction.
  internal_ranges = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "240.0.0.0/4",
  ]
}
