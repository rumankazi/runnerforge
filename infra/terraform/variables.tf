variable "image_tag" {
  description = "Container image tag in Artifact Registr (e.g., 0.1.0)"
  type = string
}

variable "github_app_id" {
  description = "Github App numeric ID - set via -var or terraform.tfvars (gitignored)"
  type = string
}
