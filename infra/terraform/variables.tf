variable "image_tag" {
  description = "Container image tag in Artifact Registr (e.g., 0.1.0)"
  type        = string
}

variable "github_app_id" {
  description = "Github App numeric ID - set via -var or terraform.tfvars (gitignored)"
  type        = string
}

variable "cloud_run_url" {
  description = "Cloud Run service URL. Used as OIDC audience for /sweep auth (both scheduler signs with this audience, and orchestrator validates against it)."
  type        = string
}
