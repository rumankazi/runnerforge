# Service account used by Cloud Scheduler to sign OIDC tokens for /sweep and
# /reconcile
resource "google_service_account" "scheduler" {
  account_id   = "runnerforge-scheduler"
  display_name = "RunnerForge Cloud Scheduler"
  description  = "Identity for Cloud Scheduler jobs that invoke the orchestrator (e.g., /sweep). The orchestrator validates the OIDC token's email claim against this SA."
  lifecycle {
    prevent_destroy = true
  }
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

# The /reconcile cron job (ADR-018), every 5 minutes. A run stops starting
# records after 120 s (reconcile.RUN_BUDGET_SECONDS, plus up to 30 s for the
# record in flight), so 180 s covers it. No retry: a retry could overlap a run
# still in progress, and the next run is at most 5 minutes away.
resource "google_cloud_scheduler_job" "reconcile" {
  name             = "runnerforge-reconcile"
  region           = "europe-west4"
  description      = "Triggers POST /reconcile on the orchestrator to bring job records in line with GitHub and GCE (ADR-018)"
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "180s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${var.cloud_run_url}/reconcile"

    # Same identity and audience as /sweep: the orchestrator accepts both.
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = var.cloud_run_url
    }
  }
}
