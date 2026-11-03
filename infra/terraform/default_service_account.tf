# The default compute service account holds roles/editor and a project-wide
# roles/iam.serviceAccountUser, so whoever gets its token can act as any account
# here, ci-terraform-apply included. Nothing runs as it: Cloud Run, Scheduler,
# the runner VMs, Packer and the image smoke test each name their own account,
# and Cloud Build, whose default account it still is, last ran on 2026-06-01.
# ADR-020 step 3e disables it.
#
# The owner disables it by hand. google_project_default_service_accounts finds
# default accounts by display name ("Compute Engine default service account"),
# and this project's older one ("Default compute service account") doesn't
# match, so that resource would do nothing here. Its grants (Editor, plus
# artifactregistry.writer, iam.serviceAccountUser, logging.logWriter and
# run.admin, granted by hand on 2026-01-15 for Cloud Build deploys) stay until
# step 9, so the rollback is to re-enable it and delete this file (merge that
# PR first: the postcondition below fails every plan while it's enabled).
#
# Every plan reads it back, so plans, applies and drift detection fail while
# it's enabled (terraform-drift runbook). Not a `check` block: its data source
# is re-read on every apply, so every plan would report changes.
data "google_service_account" "default_compute" {
  project    = data.google_project.current.project_id
  account_id = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"

  lifecycle {
    postcondition {
      condition     = self.disabled
      error_message = "The control project's default compute service account is enabled (ADR-020 step 3e requires it disabled). Disable it again (terraform-drift runbook)."
    }
  }
}

# Also keeps the data source referenced: tfaction runs `tflint --fix`, which
# deletes unused declarations.
output "default_compute_sa_disabled" {
  description = "Whether the control project's default compute service account is disabled (always true after a successful apply)."
  value       = data.google_service_account.default_compute.disabled
}
