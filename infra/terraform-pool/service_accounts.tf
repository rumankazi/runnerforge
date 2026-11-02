# Enabling Compute created a default compute service account with Editor.
# Pool VMs run with no service account (ADR-020), so it is disabled.
resource "google_project_default_service_accounts" "pool" {
  project = var.pool_project_id
  action  = "DISABLE"
  # The default, REVERT, would re-enable it if this resource were ever removed.
  restore_policy = "NONE"

  depends_on = [google_project_service.enabled]
}

# The disable call returns without waiting, and IAM reads can trail writes. On
# the first apply, give it a moment before the check below reads it back.
resource "time_sleep" "default_sa_disabled" {
  create_duration = "30s"

  depends_on = [google_project_default_service_accounts.pool]
}

data "google_project" "pool" {
  project_id = var.pool_project_id
}

# google_project_default_service_accounts never reads the account back, so this
# is what notices it being re-enabled: every plan (and drift detection) fails
# until it's disabled again. Not a `check` block: its data source is re-read on
# every apply, so every plan would report changes.
data "google_service_account" "default_compute" {
  project    = var.pool_project_id
  account_id = "${data.google_project.pool.number}-compute@developer.gserviceaccount.com"

  # Deferred to apply on the first plan, while the account is still enabled.
  depends_on = [time_sleep.default_sa_disabled]

  lifecycle {
    postcondition {
      condition     = self.disabled
      error_message = "The pool's default compute service account is enabled (ADR-020 requires it disabled). Disable it again."
    }
  }
}

# Also keeps the data source referenced: tfaction runs `tflint --fix`, which
# deletes unused declarations.
output "default_compute_sa_disabled" {
  description = "Whether the pool's default compute service account is disabled (always true after a successful apply)."
  value       = data.google_service_account.default_compute.disabled
}
