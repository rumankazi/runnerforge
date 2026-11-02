# Granted by hand before the first apply that needed it (an apply can't grant
# itself a new class of permission), then codified here for drift detection.
# roles/editor can only read log exclusions; logging.tf creates one.
resource "google_project_iam_member" "ci_terraform_apply_logging_config_writer" {
  project = var.pool_project_id
  role    = "roles/logging.configWriter"
  member  = "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"
}
