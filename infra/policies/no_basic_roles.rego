package main
import rego.v1

# Basic (primitive) roles grant far more than what's typically needed.
# Force the use of specific predefined or custom roles by failing the plan
# if any binding tries to use them.

basic_roles := {
  "roles/owner",
  "roles/editor",
  "roles/viewer"
}

# Service accounts explicitly permitted to use basic roles.
# Adding to this list requires PR + CODEOWNERS review — adds a real friction
# for new exceptions. Existing exceptions reflect S2 design decisions:
#   - ci-terraform-apply needs editor-equivalent breadth across many resource
#     types TF manages. Granular role enumeration would mean ongoing maintenance
#     every time TF starts managing a new resource type.
#   - ci-terraform-plan needs viewer-equivalent read across the same surface.
# Per-SA WIF principal pinning + CODEOWNERS on /infra/terraform/ + this OPA
# policy together are the layered defenses; the basic role grant is acceptable
# given the surrounding constraints.
allowed_basic_role_grantees := {
	"serviceAccount:ci-terraform-plan@runnerforge.iam.gserviceaccount.com",
	"serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com",
}

# Resource type that grant IAM roles to principals.
# Extend this set as new ones are added
iam_resource_type := {
  "google_project_iam_member",
  "google_project_iam_binding",
  "google_project_iam_policy",
  "google_folder_iam_member",
  "google_organization_iam_member",
  "google_service_account_iam_member",
  "google_storage_bucket_iam_member",
  "google_secret_manager_secret_iam_member",
  "google_artifact_registry_repository_iam_member",
  "google_cloud_run_v2_service_iam_member"
}

deny contains msg if {
  some i
  rc := input.resource_changes[i]
  rc.type in iam_resource_type
  role := rc.change.after.role
  role in basic_roles
  not rc.change.after.member in allowed_basic_role_grantees
  msg := sprintf(
    "Resource %q grants basic role %q. Use a specific predefined or custom role instead.",
    [rc.address, role],
  )
}
