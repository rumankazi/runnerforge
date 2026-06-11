# ------- Orchestrator service account ------

resource "google_service_account" "orchestrator" {
  account_id   = "runnerforge-orchestrator"
  display_name = "RunnerForge orchestrator (Cloud Run)"
  description  = "Identity of the orchestrator Cloud Run service. Creates/deletes runner VMs and reads Github App secrets"
  lifecycle {
    prevent_destroy = true
  }
}

# ------- Helper: default compute SA email (needed for act-as) -----


# ------- Project-level role bindings -----

resource "google_project_iam_member" "orchestrator_instance_admin" {
  project = data.google_project.current.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_image_user" {
  project = data.google_project.current.project_id
  role    = "roles/compute.imageUser"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_log_write" {
  project = data.google_project.current.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_cloud_trace_agent" {
  project = "runnerforge"
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

# Read-only access to Cloud Logging for the preemption cross-check in
# handle_completed. The orchestrator queries audit logs for
# `compute.instances.preempted` entries to distinguish spot-preemption
# failures from regular runner failures. Read-only is sufficient; write is
# already covered by `roles/logging.logWriter` above.
resource "google_project_iam_member" "orchestrator_log_viewer" {
  project = data.google_project.current.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

# ----- Secret-level grants (narrowest scope possible) ----

resource "google_secret_manager_secret_iam_member" "orchestrator_reads_webhook_secret" {
  secret_id = google_secret_manager_secret.github_webhook_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_secret_manager_secret_iam_member" "orchestrator_reads_app_private_key" {
  secret_id = google_secret_manager_secret.github_app_private_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.orchestrator.email}"
}
