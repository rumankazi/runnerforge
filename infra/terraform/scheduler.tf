# Service account used by Cloud Scheduler to sign OIDC tokens for /sweep
resource "google_service_account" "scheduler" {
  account_id   = "runnerforge-scheduler"
  display_name = "RunnerForge Cloud Scheduler"
  description  = "Identity for Cloud Scheduler jobs that invoke the orchestrator (e.g., /sweep). The orchestrator validates the OIDC token's email claim against this SA."
}

# The /sweep cron job — runs every day at 00:12
resource "google_cloud_scheduler_job" "sweep" {
  name             = "runnerforge-sweep"
  region           = "europe-west4"
  description      = "Triggers POST /sweep on the orchestrator to clean up orphan runner VMs"
  schedule         = "12 0 * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "60s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${var.cloud_run_url}/sweep"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = var.cloud_run_url
    }
  }
}
