resource "google_service_account" "ci_terraform_plan" {
  account_id   = "ci-terraform-plan"
  display_name = "RunnerForge CI Terraform Plan"
  description  = "Read-only impersonation target for plan-on-PR workflow. No write permissions"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "ci_terraform_apply" {
  account_id   = "ci-terraform-apply"
  display_name = "RunnerForge CI Terraform Apply"
  description  = "Read/Write impersonation target for apply-on-merge."
  lifecycle {
    prevent_destroy = true
  }
}

# --- Grants ---

# For ci_terraform_plan

resource "google_project_iam_member" "ci_terraform_plan_viewer" {
  project = data.google_project.current.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.ci_terraform_plan.email}"
}

resource "google_project_iam_member" "ci_terraform_plan_iam_security" {
  project = data.google_project.current.project_id
  role    = "roles/iam.securityReviewer"
  member  = "serviceAccount:${google_service_account.ci_terraform_plan.email}"
}

resource "google_storage_bucket_iam_member" "ci_terraform_plan_state_viewer" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ci_terraform_plan.email}"
}

resource "google_service_account_iam_member" "ci_terraform_plan_wif" {
  service_account_id = google_service_account.ci_terraform_plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.repository/rumankazi/runnerforge"
}

# For ci_terraform_apply

resource "google_project_iam_member" "ci_terraform_apply_editor" {
  project = data.google_project.current.project_id
  role    = "roles/editor"
  member  = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

resource "google_project_iam_member" "ci_terraform_apply_iam_security_admin" {
  project = data.google_project.current.project_id
  role    = "roles/iam.securityAdmin"
  member  = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

resource "google_project_iam_member" "ci_terraform_apply_secretmanager_admin" {
  project = data.google_project.current.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

resource "google_storage_bucket_iam_member" "ci_terraform_apply_state_admin" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

resource "google_service_account_iam_member" "ci_terraform_apply_wif" {
  service_account_id = google_service_account.ci_terraform_apply.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.workflow_path/rumankazi/runnerforge/.github/workflows/apply-terraform.yaml"
}

# --- Outputs ---

output "ci_terraform_plan_sa_email" {
  value = google_service_account.ci_terraform_plan.email
  description = "SA email impersonated by _qualify-infra.yaml workflow"
}

output "ci_terraform_apply_sa_email" {
  value = google_service_account.ci_terraform_apply.email
  description = "SA email impersonated by apply-terraform.yaml workflow"
}
