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
}
