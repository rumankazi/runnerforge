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

# What the plan account needs to refresh this root, plus the pool root's read of
# this project's metrics scope (resourcemanager.projects.get), and nothing that
# reads a VM, a log entry, a Firestore document, a secret version or an object.
# It replaced roles/viewer and roles/iam.securityReviewer (ADR-020 step 3f,
# removed below), which both list instances, whose metadata holds runner
# registration tokens. infra/policies/control_reader.rego holds the policy's
# copy of the permissions and the map of what each type reads: change them
# together. project and role_id are literal so the policy reads them at plan time.
resource "google_project_iam_custom_role" "control_terraform_reader" {
  project     = "runnerforge"
  role_id     = "controlTerraformReader"
  title       = "RunnerForge control Terraform reader"
  description = "Refreshes infra/terraform for plans and drift detection. Never add VM, disk, log-entry, Firestore-document, secret-version or object reads (ADR-020)."
  permissions = [
    "artifactregistry.repositories.get",
    "artifactregistry.repositories.getIamPolicy",
    "cloudquotas.quotas.get",
    "cloudscheduler.jobs.fullView",
    "cloudscheduler.jobs.get",
    "compute.routers.get",
    "compute.subnetworks.get",
    "datastore.databases.getMetadata",
    "datastore.schemas.get",
    "iam.googleapis.com/workloadIdentityPoolProviders.get",
    "iam.googleapis.com/workloadIdentityPools.get",
    "iam.googleapis.com/workloadIdentityPools.getAttestationRules",
    "iam.roles.get",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.getIamPolicy",
    "logging.logMetrics.get",
    "monitoring.alertPolicies.get",
    "monitoring.dashboards.get",
    "monitoring.notificationChannels.get",
    "monitoring.services.get",
    "monitoring.slos.get",
    "resourcemanager.projects.get",
    "resourcemanager.projects.getIamPolicy",
    "run.services.get",
    "run.services.getIamPolicy",
    "secretmanager.secrets.get",
    "secretmanager.secrets.getIamPolicy",
    "serviceusage.services.list",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
  ]

  # Only a role_id or project change replaces it; editing permissions doesn't.
  # A deleted custom role's ID can't be reused for weeks.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_iam_member" "ci_terraform_plan_control_reader" {
  project = "runnerforge"
  role    = "projects/runnerforge/roles/${google_project_iam_custom_role.control_terraform_reader.role_id}"
  member  = "serviceAccount:${google_service_account.ci_terraform_plan.email}"

  lifecycle {
    prevent_destroy = true
  }
}

# Grants removed by hand in ADR-020 step 3f, which must stay gone (the pool's
# step-3b removals in infra/terraform-pool/iam.tf work the same way). A
# _member_remove resource removes its grant when created and stays in state
# while the grant is absent. If the grant comes back, the next refresh drops it
# from state, so the next plan shows it to add (drift), and applying a plan
# made after that removes the grant again. Deleting one of these blocks only
# stops that check; it never restores a grant.
#
# So re-granting roles/viewer is no fix for a plan that can't read something:
# add the missing permission to controlTerraformReader by hand instead, and make
# the PR that codifies it the next control merge (terraform-drift runbook).
#
# _member_remove rewrites the project policy without the provider's IAM lock or
# its retry on a concurrent change, so each one waits for every other write to
# the project policy here (enabling an API can add a service agent to it), and
# for the removal before it. Add any new one (an IAM member or binding, an API)
# to the first removal's depends_on.
resource "google_project_iam_member_remove" "ci_terraform_plan_control_viewer" {
  project = "runnerforge"
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.ci_terraform_plan.email}"

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.orchestrator_instance_admin,
    google_project_iam_member.orchestrator_image_user,
    google_project_iam_member.orchestrator_log_write,
    google_project_iam_member.orchestrator_cloud_trace_agent,
    google_project_iam_member.orchestrator_log_viewer,
    google_project_iam_member.orchestrator_firestore_user,
    google_project_iam_member.ci_runner_image_build_instance_admin,
    google_project_iam_member.ci_runner_image_manager,
    google_project_iam_member.ci_runner_image_build_log_viewer,
    google_project_iam_member.ci_vm_logger_writer,
    google_project_iam_member.runnerforge_runner_vm_log_write,
    google_project_iam_member.ci_terraform_plan_control_reader,
    google_project_iam_member.ci_terraform_apply_iam_role_admin,
    google_project_iam_member.ci_terraform_apply_firestore_database_creator,
    google_project_iam_member.ci_terraform_apply_editor,
    google_project_iam_member.ci_terraform_apply_iam_security_admin,
    google_project_iam_member.ci_terraform_apply_secretmanager_admin,
  ]
}

resource "google_project_iam_member_remove" "ci_terraform_plan_control_security_reviewer" {
  project = "runnerforge"
  role    = "roles/iam.securityReviewer"
  member  = "serviceAccount:${google_service_account.ci_terraform_plan.email}"

  depends_on = [google_project_iam_member_remove.ci_terraform_plan_control_viewer]
}

resource "google_storage_bucket_iam_member" "ci_terraform_plan_state_user" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ci_terraform_plan.email}"
}

resource "google_service_account_iam_member" "ci_terraform_plan_wif" {
  service_account_id = google_service_account.ci_terraform_plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.repository/rumankazi/runnerforge"
}

# For ci_terraform_apply

# Allows ci-terraform-apply to create + update custom IAM roles
# The narrower iam.securityAdmin above covers IAM policies/bindings but not role definitions themselves.
resource "google_project_iam_member" "ci_terraform_apply_iam_role_admin" {
  project = data.google_project.current.project_id
  role    = "roles/iam.roleAdmin"
  member  = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

# Lets ci-terraform-apply create Firestore databases (firestore.tf). roles/editor
# covers database updates, indexes and TTL fields, but not
# datastore.databases.create or datastore.locations.get. No databases.delete on
# purpose: the job store is delete-protected.
# Bootstrapped out-of-band with gcloud before the first apply (the apply SA can't
# grant itself a new permission in the apply that needs it); the import adopts
# the role, and re-adding the existing member is a no-op.
import {
  to = google_project_iam_custom_role.ci_firestore_database_creator
  id = "projects/runnerforge/roles/firestoreDatabaseCreator"
}

resource "google_project_iam_custom_role" "ci_firestore_database_creator" {
  role_id     = "firestoreDatabaseCreator"
  title       = "RunnerForge Firestore Database Creator (CI)"
  description = "Create-only Firestore database permissions for the ci-terraform-apply SA (ADR-018)."
  project     = data.google_project.current.project_id
  permissions = [
    "datastore.databases.create",
    "datastore.locations.get",
    "datastore.locations.list",
  ]
}

resource "google_project_iam_member" "ci_terraform_apply_firestore_database_creator" {
  project = data.google_project.current.project_id
  role    = google_project_iam_custom_role.ci_firestore_database_creator.name
  member  = "serviceAccount:${google_service_account.ci_terraform_apply.email}"
}

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

# The apply account's only WIF binding: only jobs in the prod-infra environment
# (branch main, owner approval) get the apply token. It replaced a binding on
# attribute.workflow_path, which any branch's copy of deploy-infra.yaml matched
# (ADR-020 step 3). The subject is the repo's default OIDC sub (use_default:
# true, use_immutable_subject: false). Changing either setting, or renaming the
# repo or the environment, breaks both apply jobs until a binding for the new
# subject is added by hand, then codified as a new resource: editing member
# here is a replace, which prevent_destroy blocks (security.mdx, CI identity).
resource "google_service_account_iam_member" "ci_terraform_apply_wif_env" {
  service_account_id = google_service_account.ci_terraform_apply.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/subject/repo:rumankazi/runnerforge:environment:prod-infra"

  lifecycle {
    prevent_destroy = true
  }
}

# --- Outputs ---

output "ci_terraform_plan_sa_email" {
  value       = google_service_account.ci_terraform_plan.email
  description = "SA email impersonated by _qualify-infra.yaml workflow"
}

output "ci_terraform_apply_sa_email" {
  value       = google_service_account.ci_terraform_apply.email
  description = "SA email impersonated by apply-terraform.yaml workflow"
}
