resource "google_storage_bucket" "terraform_state" {
  name     = "runnerforge-terraform-state"
  location = "EUROPE-WEST4"
  project  = "runnerforge"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true # This bucked holds TF state itself - never let TF destroy it
  }
}
