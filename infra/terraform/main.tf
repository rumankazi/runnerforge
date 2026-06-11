terraform {
  backend "gcs" {
    bucket = "runnerforge-terraform-state"
    prefix = "orchestrator"
  }
}

provider "google" {
  project = "runnerforge"
  region  = "europe-west4"
  zone    = "europe-west4-a"
}

# Aliased provider used only by Cloud Quotas resources. The Cloud Quotas API
# requires the X-Goog-User-Project header (user_project_override=true) so the
# API can attribute billing/quota to the caller's project. The override is
# scoped to this alias so the default provider's behavior is unchanged for
# every other resource type.
provider "google" {
  alias                 = "quotas"
  project               = "runnerforge"
  region                = "europe-west4"
  zone                  = "europe-west4-a"
  user_project_override = true
  billing_project       = "runnerforge"
}

resource "google_artifact_registry_repository" "orchestrator" {
  location      = "europe-west4"
  repository_id = "orchestrator"
  format        = "DOCKER"
  description   = "RunnerForge orchestrator container images"

  cleanup_policies {
    id     = "keep-recent-tagged"
    action = "KEEP"
    most_recent_versions {
      package_name_prefixes = ["orchestrator"]
      keep_count            = 5
    }
  }

  cleanup_policies {
    id     = "delete-untagged-old"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }
}
