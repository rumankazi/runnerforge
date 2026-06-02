data "google_project" "current" {}

resource "google_iam_workload_identity_pool" "github_pool" {
  workload_identity_pool_id = "github"
}

resource "google_iam_workload_identity_pool_provider" "github_pool_provider" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "Github Provider"
  description                        = "Github Actions identity pool provider"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
  attribute_condition = "assertion.repository_owner == 'rumankazi' && assertion.repository == 'rumankazi/runnerforge' && assertion.ref == 'refs/heads/main'"
  attribute_mapping = {
    "google.subject"             = "assertion.sub",
    "attribute.repository"       = "assertion.repository",
    "attribute.repository_owner" = "assertion.repository_owner",
    "attribute.ref"              = "assertion.ref"
  }

}

resource "google_service_account" "ci_build" {
  account_id   = "ci-build"
  display_name = "RunnerForge CI Build"
  description  = "Impersonated by GitHub Actions for orchestrator image build + Cloud Run deploy"
}

resource "google_service_account_iam_member" "ci_build_wif" {
  service_account_id = google_service_account.ci_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.repository/rumankazi/runnerforge"
}

resource "google_service_account" "ci_terraform" {
  account_id   = "ci-terraform"
  display_name = "RunnerForge CI Terraform"
  description  = "Impersonated by GitHub Actions for terraform apply (state, IAM, secrets, scheduler)"
}

resource "google_service_account_iam_member" "ci_terraform_wif" {
  service_account_id = google_service_account.ci_terraform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.repository/rumankazi/runnerforge"
}

# --- Project-level perms for the 2.1 smoke ---
# Both SAs only need read access for `gcloud projects describe`. Widened in 2.2 / 2.4.

resource "google_project_iam_member" "ci_build_viewer" {
  project = data.google_project.current.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.ci_build.email}"
}

resource "google_project_iam_member" "ci_terraform_viewer" {
  project = data.google_project.current.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.ci_terraform.email}"
}

# --- Outputs the workflow file needs ---

output "wif_provider_name" {
  value       = google_iam_workload_identity_pool_provider.github_pool_provider.name
  description = "Full provider resource name. Use as `workload_identity_provider` in google-github-actions/auth."
}

output "ci_build_sa_email" {
  value       = google_service_account.ci_build.email
  description = "SA email impersonated by build/deploy workflows."
}

output "ci_terraform_sa_email" {
  value       = google_service_account.ci_terraform.email
  description = "SA email impersonated by terraform-apply workflows."
}
