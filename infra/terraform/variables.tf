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

variable "allowed_installation_ids" {
  description = "GitHub App installation IDs allowed to create runner VMs. Must stay in place before and after the App is made public: every new tenant is added here explicitly (manual approval during the free beta). An empty list allows nobody."
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
