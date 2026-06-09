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

variable "cloud_run_url" {
  description = "Cloud Run service URL. Used as OIDC audience for /sweep auth (both scheduler signs with this audience, and orchestrator validates against it)."
  type        = string
  default     = "https://runnerforge-orchestrator-gsqedgmwha-ez.a.run.app"
}
