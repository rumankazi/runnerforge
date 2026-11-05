package main

import rego.v1

# Every resource and data source type in infra/terraform after step 3e.
cr_config := [
	rd_config("data", "google_project", "current"),
	rd_config("data", "google_service_account", "default_compute"),
	rd_config("data", "google_compute_subnetwork", "default"),
	rd_config("managed", "google_project_service", "enabled"),
	rd_config("managed", "google_project_iam_member", "ci_terraform_plan_control_reader"),
	rd_config("managed", "google_project_iam_custom_role", "control_terraform_reader"),
	rd_config("managed", "google_service_account", "ci_terraform_plan"),
	rd_config("managed", "google_service_account_iam_member", "ci_terraform_plan_wif"),
	rd_config("managed", "google_iam_workload_identity_pool", "github_pool"),
	rd_config("managed", "google_iam_workload_identity_pool_provider", "github_pool_provider"),
	rd_config("managed", "google_storage_bucket", "terraform_state"),
	rd_config("managed", "google_storage_bucket_iam_member", "ci_terraform_plan_state_user"),
	rd_config("managed", "google_artifact_registry_repository", "runnerforge"),
	rd_config("managed", "google_artifact_registry_repository_iam_member", "ci_build_writer"),
	rd_config("managed", "google_cloud_run_v2_service", "orchestrator"),
	rd_config("managed", "google_cloud_run_v2_service_iam_member", "scheduler_invoker"),
	rd_config("managed", "google_cloud_scheduler_job", "sweep"),
	rd_config("managed", "google_secret_manager_secret", "github_webhook_secret"),
	rd_config("managed", "google_secret_manager_secret_iam_member", "orchestrator_webhook_secret"),
	rd_config("managed", "google_firestore_database", "runnerforge"),
	rd_config("managed", "google_firestore_index", "jobs_by_status"),
	rd_config("managed", "google_firestore_field", "jobs_expire_at"),
	rd_config("managed", "google_compute_router", "runnerforge"),
	rd_config("managed", "google_compute_router_nat", "runnerforge"),
	rd_config("managed", "google_logging_metric", "webhook_received"),
	rd_config("managed", "google_monitoring_alert_policy", "webhook_errors"),
	rd_config("managed", "google_monitoring_slo", "webhook_availability"),
	rd_config("managed", "google_monitoring_service", "orchestrator"),
	rd_config("managed", "google_monitoring_notification_channel", "owner_email"),
	rd_config("managed", "google_monitoring_dashboard", "orchestrator_health"),
	rd_config("managed", "google_cloud_quotas_quota_preference", "n2_cpus"),
	rd_config("managed", "time_sleep", "wait_for_log_metrics"),
]

cr_provider_config := {"google": {"expressions": {"project": {"constant_value": "runnerforge"}}}}

cr_input(config, changes) := {
	"variables": {"github_app_id": {"value": "1"}},
	"configuration": {"provider_config": cr_provider_config, "root_module": {"resources": config}},
	"resource_changes": changes,
}

cr_messages(config, changes) := m if {
	m := deny with input as cr_input(config, changes)
}

cr_has(config, changes, fragment) if {
	some msg in cr_messages(config, changes)
	contains(msg, fragment)
}

cr_role(permissions) := object.union(po_role("control_terraform_reader", "controlTerraformReader", permissions), {"change": {"after": {"project": "runnerforge"}}})

cr_reader(extra) := cr_role(array.concat(sort(control_reader_permissions), extra))

cr_reader_without(p) := cr_role(sort(control_reader_permissions - {p}))

test_control_full_config_passes if {
	count(cr_messages(cr_config, [cr_reader([])])) == 0
}

test_control_unmapped_type_fails if {
	cr_has(array.concat(cr_config, [rd_config("managed", "google_compute_instance", "probe")]), [cr_reader([])], "Unmapped type google_compute_instance")
	cr_has(array.concat(cr_config, [rd_config("data", "google_compute_network", "default")]), [cr_reader([])], "Unmapped type data.google_compute_network")
}

test_control_missing_mapped_permission_fails if {
	cr_has(cr_config, [cr_reader_without("storage.buckets.getIamPolicy")], `can't refresh google_storage_bucket_iam_member: it lacks "storage.buckets.getIamPolicy"`)
	cr_has(cr_config, [cr_reader_without("iam.googleapis.com/workloadIdentityPools.getAttestationRules")], `can't refresh google_iam_workload_identity_pool: it lacks "iam.googleapis.com/workloadIdentityPools.getAttestationRules"`)
	cr_has(cr_config, [cr_reader_without("datastore.schemas.get")], `can't refresh google_firestore_field: it lacks "datastore.schemas.get"`)
}

test_control_pool_need_is_kept_without_a_control_type_needing_it if {
	config := [rd_config("managed", "google_logging_metric", "webhook_received")]
	cr_has(config, [cr_reader_without("resourcemanager.projects.get")], `lacks "resourcemanager.projects.get", which google_monitoring_monitored_project.control_scope`)
}

test_control_role_missing_or_destroyed_fails if {
	cr_has(cr_config, [], "has no controlTerraformReader custom role")
	gone := {
		"address": "google_project_iam_custom_role.control_terraform_reader",
		"mode": "managed",
		"type": "google_project_iam_custom_role",
		"change": {"actions": ["delete"], "after": null},
	}
	cr_has(cr_config, [gone], "has no controlTerraformReader custom role")
}

test_control_role_in_another_project_does_not_count if {
	other := object.union(cr_reader([]), {"change": {"after": {"project": "elsewhere"}}})
	cr_has(cr_config, [other], "has no controlTerraformReader custom role")
}

test_control_module_call_fails if {
	i := object.union(cr_input(cr_config, [cr_reader([])]), {"configuration": {"root_module": {"module_calls": {"extra": {"source": "./extra"}}}}})
	some msg in deny with input as i
	contains(msg, `The control module calls modules ["extra"]`)
}

test_control_unknown_role_fields_fail if {
	no_perms := json.remove(cr_reader([]), ["change/after/permissions"])
	cr_has(cr_config, [no_perms], "has a project, role_id or permissions unknown at plan time")
	partly := object.union(cr_reader([]), {"change": {"after_unknown": {"permissions": [false, true]}}})
	cr_has(cr_config, [partly], "has a project, role_id or permissions unknown at plan time")
	no_id := json.remove(cr_reader([]), ["change/after/role_id"])
	cr_has(cr_config, [no_id, cr_reader([])], "has a project, role_id or permissions unknown at plan time")
	no_project := json.remove(cr_reader([]), ["change/after/project"])
	cr_has(cr_config, [no_project, cr_reader([])], "has a project, role_id or permissions unknown at plan time")
}

test_control_extra_permissions_fail if {
	every p in [
		"compute.instances.list",
		"compute.instances.getSerialPortOutput",
		"datastore.entities.get",
		"secretmanager.versions.access",
		"logging.logEntries.list",
		"storage.objects.get",
		"artifactregistry.repositories.downloadArtifacts",
		"resourcemanager.projects.setIamPolicy",
	] {
		cr_has(cr_config, [cr_reader([p])], sprintf(`(controlTerraformReader) adds [%q]`, [p]))
	}
}

# The ceiling holds in any target, so the pool target can't widen it either.
test_control_ceiling_holds_outside_the_control_target if {
	some msg in deny with input as {"resource_changes": [cr_reader(["compute.instances.get"])]}
	contains(msg, `(controlTerraformReader) adds ["compute.instances.get"]`)
}

test_control_reader_permissions_are_settings_reads if {
	count({p | some p in control_reader_permissions; control_reader_bad(p)}) == 0
}

test_control_reader_bad_catches_data_reads_and_writes if {
	every p in [
		"compute.instances.get",
		"compute.disks.get",
		"compute.projects.get",
		"compute.machineImages.get",
		"compute.instanceTemplates.get",
		"compute.regionInstanceTemplates.get",
		"compute.regionDisks.get",
		"compute.instanceSettings.get",
		"cloudtasks.tasks.fullView",
		"compute.images.useReadOnly",
		"datastore.databases.get",
		"datastore.entities.list",
		"secretmanager.versions.get",
		"logging.logEntries.list",
		"logging.views.get",
		"storage.objects.list",
		"artifactregistry.files.get",
		"apikeys.keys.getKeyString",
		"cloudtrace.traces.get",
		"cloudbuild.builds.get",
		"bigquery.tables.getData",
		"monitoring.timeSeries.list",
		"run.services.sshRead",
		"iam.serviceAccounts.getAccessToken",
		"iam.serviceAccountKeys.create",
		"resourcemanager.projects.setIamPolicy",
	] {
		control_reader_bad(p)
	}
}

# Editing the policy's copy along with the role still fails.
test_control_policy_copy_is_checked_too if {
	widened := control_reader_permissions | {"compute.instances.get"}
	some msg in deny with input as cr_input(cr_config, [cr_reader(["compute.instances.get"])])
		with data.main.control_reader_permissions as widened
	contains(msg, `control_reader_permissions lists "compute.instances.get"`)
	some msg2 in deny with input as cr_input(cr_config, [cr_reader(["compute.instances.get"])])
		with data.main.control_reader_permissions as widened
	contains(msg2, `(controlTerraformReader) adds "compute.instances.get"`)
}

test_target_neither_fails if {
	some msg in deny with input as {"configuration": {"root_module": {"resources": []}}, "resource_changes": []}
	contains(msg, "matches neither tfaction target")
}

test_target_both_fails if {
	i := object.union(cr_input(cr_config, [cr_reader([])]), {"variables": un_pool_vars})
	some msg in deny with input as i
	contains(msg, "matches both the control and the pool target")
}

test_target_pool_provider_is_not_control if {
	i := object.union(rd_input(rd_pool_config, [po_reader_role([])]), {"configuration": {"provider_config": {"google": {"expressions": {"project": {"references": ["var.pool_project_id"]}}}}}})
	count(deny) == 0 with input as i
}

test_target_unit_input_without_configuration_is_unaffected if {
	count(deny) == 0 with input as {"resource_changes": []}
}

test_pool_target_custom_role_in_control_fails if {
	some msg in deny with input as {"variables": un_pool_vars, "resource_changes": [cr_reader([])]}
	contains(msg, `"google_project_iam_custom_role.control_terraform_reader" declares a custom role in project runnerforge from the pool target`)
}

cr_plan_member(name, role, actions) := {
	"address": sprintf("google_project_iam_member.%s", [name]),
	"mode": "managed",
	"type": "google_project_iam_member",
	"change": {"actions": actions, "after": {"project": "runnerforge", "role": role, "member": pool_plan_member}},
}

cr_regrant(changes, address) if {
	some msg in deny with input as {"resource_changes": changes}
	contains(msg, sprintf("%q would grant", [address]))
}

test_plan_broad_control_grants_unchanged_pass if {
	count(deny) == 0 with input as {"resource_changes": [
		cr_plan_member("ci_terraform_plan_viewer", "roles/viewer", ["no-op"]),
		cr_plan_member("ci_terraform_plan_iam_security", "roles/iam.securityReviewer", ["no-op"]),
	]}
}

test_plan_broad_control_grants_regranted_fail if {
	cr_regrant([cr_plan_member("ci_terraform_plan_viewer", "roles/viewer", ["create"])], "google_project_iam_member.ci_terraform_plan_viewer")
	cr_regrant([cr_plan_member("ci_terraform_plan_iam_security", "roles/iam.securityReviewer", ["create"])], "google_project_iam_member.ci_terraform_plan_iam_security")
	cr_regrant([cr_plan_member("v", "roles/viewer", ["delete", "create"])], "google_project_iam_member.v")
}

test_plan_broad_control_grant_upper_case_or_binding_fails if {
	upper := object.union(cr_plan_member("v", "roles/viewer", ["create"]), {"change": {"after": {"member": "serviceAccount:CI-Terraform-Plan@runnerforge.iam.gserviceaccount.com"}}})
	cr_regrant([upper], "google_project_iam_member.v")
	b := {
		"address": "google_project_iam_binding.v",
		"mode": "managed",
		"type": "google_project_iam_binding",
		"change": {"actions": ["update"], "after": {"project": "runnerforge", "role": "roles/viewer", "members": [pool_plan_member]}},
	}
	cr_regrant([b], "google_project_iam_binding.v")
}

test_plan_broad_grant_in_pool_is_left_to_pool_reader if {
	m := object.union(cr_plan_member("v", "roles/viewer", ["create"]), {"change": {"after": {"project": "runnerforge-pool"}}})
	not cr_regrant([m], "google_project_iam_member.v")
}

test_plan_reader_grant_passes if {
	count(deny) == 0 with input as {"resource_changes": [cr_plan_member("ci_terraform_plan_control_reader", "projects/runnerforge/roles/controlTerraformReader", ["create"])]}
}
