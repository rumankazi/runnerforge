variable "image_tag" {
  description = "Container image tag in Artifact Register (e.g., 0.1.0)"
  type        = string
  default     = "0.6.0" # bootstrap version - deploy-orchestrator updates service image automatically
}

variable "github_app_id" {
  description = "Github App numeric ID - set via -var or terraform.tfvars (gitignored)"
  type        = string
  default     = "3851412"
}

variable "admission" {
  description = "How queued jobs are admitted (ADR-019). \"registry\": installations in allowed_installation_ids always, unlimited; every other installation needs an approved tenant-registry row and gets its limits. \"allowlist\": only allowed_installation_ids, unlimited. Takes effect with the next orchestrator deploy."
  type        = string
  default     = "registry"

  validation {
    condition     = contains(["allowlist", "registry"], var.admission)
    error_message = "admission must be \"allowlist\" or \"registry\"."
  }
}

variable "allowed_installation_ids" {
  description = "GitHub App installation IDs that are always admitted, unlimited, without a registry read (the owner's). With admission = \"registry\" every other installation still needs an approved registry row, and an empty list means none bypasses the registry. With admission = \"allowlist\" only these get VMs, and an empty list allows nobody: never roll back to allowlist with an empty list."
  type        = list(string)
  default = [
    "135399152", # rumankazi (dogfood: rumankazi/runnerforge)
  ]

  validation {
    condition     = alltrue([for id in var.allowed_installation_ids : can(regex("^[0-9]+$", id))])
    error_message = "Installation IDs must be numeric strings."
  }
}

variable "cloud_run_url" {
  description = "Cloud Run service URL. Used as OIDC audience for /sweep auth (both scheduler signs with this audience, and orchestrator validates against it)."
  type        = string
  default     = "https://runnerforge-orchestrator-gsqedgmwha-ez.a.run.app"
}
