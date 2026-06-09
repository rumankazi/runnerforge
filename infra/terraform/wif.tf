resource "google_iam_workload_identity_pool" "github_pool" {
  workload_identity_pool_id = "github"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_iam_workload_identity_pool_provider" "github_pool_provider" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "Github Provider"
  description                        = "Github Actions identity pool provider"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  # Two-clause condition: identity must originate from a workflow IN this repo.
  # Per-workflow restrictions (e.g., "only apply-terraform.yaml can use the apply SA")
  # are enforced at the per-SA wif binding level via `attribute.workflow_path`.
  attribute_condition = join(" && ", [
    "assertion.repository_owner == 'rumankazi'",
    "assertion.repository == 'rumankazi/runnerforge'"
  ])

  attribute_mapping = {
    "google.subject"             = "assertion.sub",
    "attribute.repository"       = "assertion.repository",
    "attribute.repository_owner" = "assertion.repository_owner",
    "attribute.ref"              = "assertion.ref",
    "attribute.workflow_path"    = "assertion.job_workflow_ref.split('@')[0]"
  }

  lifecycle {
    prevent_destroy = true
  }

}

resource "google_service_account" "ci_build" {
  account_id   = "ci-build"
  display_name = "RunnerForge CI Build"
  description  = "Impersonated by GitHub Actions for orchestrator image build + Cloud Run deploy"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account_iam_member" "ci_build_wif" {
  service_account_id = google_service_account.ci_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.workflow_path/rumankazi/runnerforge/.github/workflows/deploy-orchestrator.yaml"
}

# --- ci-build CD perms ---
# Minimum perms to: push orchestrator images to AR, deploy Cloud Run revisions,
# and run those revisions as the orchestrator runtime SA. No secret/scheduler
# admin — those stay with ci-terraform.

resource "google_artifact_registry_repository_iam_member" "ci_build_ar_writer" {
  project    = data.google_project.current.project_id
  location   = google_artifact_registry_repository.orchestrator.location
  repository = google_artifact_registry_repository.orchestrator.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci_build.email}"
}

resource "google_cloud_run_v2_service_iam_member" "ci_build_run_developer" {
  project  = data.google_project.current.project_id
  location = google_cloud_run_v2_service.orchestrator.location
  name     = google_cloud_run_v2_service.orchestrator.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.ci_build.email}"
}

resource "google_service_account_iam_member" "ci_build_act_as_orchestrator" {
  service_account_id = google_service_account.orchestrator.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci_build.email}"
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
