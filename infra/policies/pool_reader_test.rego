package main

import rego.v1

rd_config(mode, type, name) := {
	"address": rd_address(mode, type, name),
	"mode": mode,
	"type": type,
	"name": name,
}

rd_address("managed", type, name) := sprintf("%s.%s", [type, name])

rd_address("data", type, name) := sprintf("data.%s.%s", [type, name])

# Every resource and data source in infra/terraform-pool after steps 1-3.
rd_pool_config := [
	rd_config("data", "google_project", "pool"),
	rd_config("data", "google_service_account", "default_compute"),
	rd_config("managed", "google_project_service", "enabled"),
	rd_config("managed", "google_project_default_service_accounts", "pool"),
	rd_config("managed", "time_sleep", "default_sa_disabled"),
	rd_config("managed", "time_sleep", "wait_for_pool_log_metrics"),
	rd_config("managed", "google_compute_project_metadata", "pool"),
	rd_config("managed", "google_compute_network", "pool"),
	rd_config("managed", "google_compute_subnetwork", "pool"),
	rd_config("managed", "google_compute_address", "nat"),
	rd_config("managed", "google_compute_router", "pool"),
	rd_config("managed", "google_compute_router_nat", "pool"),
	rd_config("managed", "google_compute_firewall", "deny_internal_egress"),
	rd_config("managed", "google_dns_policy", "pool"),
	rd_config("managed", "google_logging_project_exclusion", "dns_sample"),
	rd_config("managed", "google_logging_metric", "pool_vm"),
	rd_config("managed", "google_monitoring_notification_channel", "owner_email"),
	rd_config("managed", "google_monitoring_alert_policy", "pool_log_volume"),
	rd_config("managed", "google_cloud_quotas_quota_preference", "pool"),
	rd_config("managed", "google_project_iam_member", "ci_terraform_apply_pool_editor"),
	rd_config("managed", "google_project_iam_member_remove", "plan_viewer"),
	rd_config("managed", "google_project_iam_custom_role", "terraform_reader"),
	rd_config("managed", "google_compute_subnetwork_iam_member", "orchestrator_pool_subnet"),
	rd_config("managed", "google_monitoring_monitored_project", "control_scope"),
]

rd_input(config, changes) := {
	"variables": un_pool_vars,
	"configuration": {"root_module": {"resources": config}},
	"resource_changes": changes,
}

rd_messages(config, changes) := m if {
	m := deny with input as rd_input(config, changes)
}

rd_has(config, changes, fragment) if {
	some msg in rd_messages(config, changes)
	contains(msg, fragment)
}

rd_reader_without(p) := po_role("terraform_reader", "poolTerraformReader", sort(pool_reader_permissions - {p}))

test_reader_full_pool_config_passes if {
	count(rd_messages(rd_pool_config, [po_reader_role([])])) == 0
}

test_reader_extra_permissions_fail if {
	every p in ["monitoring.timeSeries.list", "storage.objects.get", "compute.instances.list"] {
		rd_has(rd_pool_config, [po_reader_role([p])], sprintf(`(poolTerraformReader) adds [%q]`, [p]))
	}
}

test_reader_unmapped_type_fails if {
	config := array.concat(rd_pool_config, [rd_config("managed", "google_compute_instance", "probe")])
	rd_has(config, [po_reader_role([])], "Unmapped type google_compute_instance")
}

test_reader_unmapped_data_source_fails if {
	# The map is keyed by mode: a data source needs its own entry.
	config := array.concat(rd_pool_config, [rd_config("data", "google_compute_network", "pool")])
	rd_has(config, [po_reader_role([])], "Unmapped type data.google_compute_network")
}

test_reader_missing_mapped_permission_fails if {
	rd_has(rd_pool_config, [rd_reader_without("dns.policies.get")], `can't refresh google_dns_policy: it lacks "dns.policies.get"`)
	rd_has(rd_pool_config, [rd_reader_without("compute.subnetworks.getIamPolicy")], `can't refresh google_compute_subnetwork_iam_member: it lacks "compute.subnetworks.getIamPolicy"`)
}

test_reader_role_missing_from_pool_plan_fails if {
	rd_has(rd_pool_config, [], "has no poolTerraformReader custom role")
	rd_has(rd_pool_config, [], `it lacks "resourcemanager.projects.get"`)
}

test_reader_role_being_destroyed_fails if {
	gone := {
		"address": "google_project_iam_custom_role.terraform_reader",
		"mode": "managed",
		"type": "google_project_iam_custom_role",
		"change": {"actions": ["delete"], "after": null},
	}
	rd_has(rd_pool_config, [gone], "has no poolTerraformReader custom role")
}

test_reader_module_call_fails if {
	i := object.union(rd_input(rd_pool_config, [po_reader_role([])]), {"configuration": {"root_module": {"module_calls": {"extra": {"source": "./extra"}}}}})
	some msg in deny with input as i
	contains(msg, `calls modules ["extra"]`)
}

test_reader_control_target_is_unaffected if {
	config := [
		rd_config("data", "google_project", "current"),
		rd_config("managed", "google_cloud_run_v2_service", "orchestrator"),
		rd_config("managed", "google_firestore_database", "runnerforge"),
	]
	count(deny) == 0 with input as {
		"variables": {"project_id": {"value": "runnerforge"}},
		"configuration": {"root_module": {"resources": config, "module_calls": {"x": {}}}},
		"resource_changes": [],
	}
}

rd_plan_member(name, role) := po_member(name, role, pool_plan_member)

rd_plan_messages(changes) := m if {
	m := deny with input as {"resource_changes": changes}
}

rd_plan_flags(changes, address) if {
	some msg in rd_plan_messages(changes)
	contains(msg, sprintf("%q grants", [address]))
	contains(msg, "to ci-terraform-plan in the pool")
}

test_plan_sa_reader_grant_passes if {
	count(rd_plan_messages([rd_plan_member("ci_terraform_plan_pool_reader", "projects/runnerforge-pool/roles/poolTerraformReader")])) == 0
	count(rd_plan_messages([rd_plan_member("ci_terraform_plan_pool_monitoring", "roles/monitoring.viewer")])) == 0
}

test_plan_sa_other_pool_roles_fail if {
	rd_plan_flags([rd_plan_member("c", "roles/compute.viewer")], "google_project_iam_member.c")
	rd_plan_flags([rd_plan_member("l", "roles/logging.viewer")], "google_project_iam_member.l")
	rd_plan_flags([rd_plan_member("v", "roles/viewer")], "google_project_iam_member.v")
}

test_plan_sa_pool_binding_fails if {
	b := po_rc("google_project_iam_binding", "b", {"project": "runnerforge-pool", "role": "roles/iam.securityReviewer", "members": [pool_plan_member]})
	rd_plan_flags([b], "google_project_iam_binding.b")
}

test_plan_sa_upper_case_fails if {
	rd_plan_flags([po_member("c", "roles/compute.viewer", "serviceAccount:CI-Terraform-Plan@runnerforge.iam.gserviceaccount.com")], "google_project_iam_member.c")
}

test_plan_sa_token_creator_on_pool_sa_from_control_fails if {
	sa := {
		"address": "google_service_account_iam_member.plan_token",
		"mode": "managed",
		"type": "google_service_account_iam_member",
		"change": {"actions": ["create"], "after": {
			"service_account_id": "projects/runnerforge-pool/serviceAccounts/355964341703-compute@developer.gserviceaccount.com",
			"role": "roles/iam.serviceAccountTokenCreator",
			"member": pool_plan_member,
		}},
	}
	some msg in deny with input as {"variables": {"image_tag": {"value": "x"}}, "resource_changes": [sa]}
	contains(msg, "\"google_service_account_iam_member.plan_token\" grants roles/iam.serviceAccountTokenCreator to ci-terraform-plan in the pool")
}

test_plan_sa_in_control_project_is_ignored if {
	m := object.union(rd_plan_member("ci_terraform_plan_iam_security", "roles/iam.securityReviewer"), {"change": {"after": {"project": "runnerforge"}}})
	count(rd_plan_messages([m])) == 0
}
