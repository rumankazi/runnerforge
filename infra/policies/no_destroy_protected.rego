package main

import rego.v1

# Resources where destruction = disaster. Matches the S3 prevent_destroy
# allowlist (intentional 1:1). Modifying this set requires PR + CODEOWNERS
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
