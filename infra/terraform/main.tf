terraform {
  backend "gcs" {
    bucket = "runnerforge-terraform-state"
    prefix = "orchestrator"
  }
}

provider "google" {
  project = "runnerforge"
  region = "europe-west4"
  zone = "europe-west4-a"
}

resource "google_artifact_registry_repository" "orchestrator" {
  location = "europe-west4"
  repository_id = "orchestrator"
  format = "DOCKER"
  description = "RunnerForge orchestrator container images"

  cleanup_policies {
    id = "keep-recent-tagged"
    action = "KEEP"
    most_recent_versions {
      package_name_prefixes = ["orchestrator"]
      keep_count = 5
    }
  }

  cleanup_policies {
    id = "delete-untagged-old"
    action = "DELETE"
    condition {
      tag_state = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }
}
