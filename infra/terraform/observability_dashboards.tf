# Cloud Monitoring dashboards.
# JSON contents live under dashboards/ subdir; edit them there (or re-export
# from the console with `gcloud monitoring dashboards describe ... --format=json`)
# and `terraform apply` to push changes.
resource "google_monitoring_dashboard" "orchestrator_health" {
  # Project must match the form stored at import time (project number,
  # not ID), otherwise TF would destroy + recreate the dashboard.
  project        = data.google_project.current.number
  dashboard_json = file("${path.module}/dashboards/orchestrator_health.json")
}
