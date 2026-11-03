package main

import rego.v1

# Resources where destruction = disaster, in the control module
# (infra/terraform) and the pool module (infra/terraform-pool). Every address
# here also has prevent_destroy in its module; the reverse doesn't hold (the
# routers and the pool's NAT addresses rely on prevent_destroy alone).
# google_monitoring_monitored_project.control_scope also sets
# deletion_policy = "PREVENT". Modifying this set requires PR + CODEOWNERS
# review. To actually destroy one of these resources requires:
#   (1) remove prevent_destroy from its lifecycle (PR 1)
#   (2) plan the destroy — OPA still blocks via this allowlist (PR 2)
#   (3) remove from this allowlist (PR 3)
# Three layers of friction for a genuinely dangerous operation.
protected_resources := {
	"google_storage_bucket.terraform_state",
	"google_iam_workload_identity_pool.github_pool",
	"google_iam_workload_identity_pool_provider.github_pool_provider",
	"google_service_account.ci_build",
	"google_service_account.ci_runner_image_build",
	"google_service_account.ci_vm_logger",
	"google_service_account.ci_terraform_plan",
	"google_service_account.ci_terraform_apply",
	"google_service_account.orchestrator",
	"google_service_account.runnerforge_runner_vm",
	"google_service_account.scheduler",
	"google_cloud_run_v2_service.orchestrator",
	"google_secret_manager_secret.github_webhook_secret",
	"google_secret_manager_secret.github_app_private_key",
	"google_firestore_database.runnerforge",
	# Pool root module (infra/terraform-pool, ADR-020). Addresses are per state,
	# so a control resource with the same address is protected too.
	"google_project_iam_member.ci_terraform_apply_pool_editor",
	"google_project_iam_member.ci_terraform_apply_pool_security_admin",
	"google_project_iam_member.ci_terraform_apply_pool_role_admin",
	"google_project_iam_member.ci_terraform_plan_pool_reader",
	"google_project_iam_member.ci_terraform_apply_logging_config_writer",
	"google_project_iam_custom_role.terraform_reader",
	"google_monitoring_monitored_project.control_scope",
}

deny contains msg if {
	some i
	rc := input.resource_changes[i]
	rc.address in protected_resources
	"delete" in rc.change.actions
	msg := sprintf(
		"Resource %q is in the protected-destroy allowlist (catches both direct destroy and replace actions). Destroying it requires removing it from protected_resources in policies/no_destroy_protected.rego first — a deliberate 3-PR procedure.",
		[rc.address],
	)
}
