package main

import rego.v1

br_apply := "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"

br_plan := "serviceAccount:ci-terraform-plan@runnerforge.iam.gserviceaccount.com"

br_rc(type, name, after) := {
	"address": sprintf("%s.%s", [type, name]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": after},
}

br_member(name, project, role, member) := br_rc("google_project_iam_member", name, {"project": project, "role": role, "member": member})

br_messages(changes) := m if {
	m := deny with input as {"resource_changes": changes}
}

br_flags(changes, address, role) if {
	some msg in br_messages(changes)
	contains(msg, sprintf("Resource %q grants basic role %q", [address, role]))
}

# Should fire when owner is granted
test_denies_owner_role if {
	"Resource \"google_project_iam_member.bad\" grants basic role \"roles/owner\" to serviceAccount:test@example.iam.gserviceaccount.com in project runnerforge. Use a specific predefined or custom role instead (allowed exceptions: basic_role_allowlist in policies/no_basic_roles.rego)." in br_messages([br_member("bad", "runnerforge", "roles/owner", "serviceAccount:test@example.iam.gserviceaccount.com")])
}

# Should NOT fire for a specific role
test_allows_specific_role if {
	count(br_messages([br_member("good", "runnerforge", "roles/run.developer", "serviceAccount:test@example.iam.gserviceaccount.com")])) == 0
}

# Should NOT fire for non-IAM resources
test_allows_non_iam_resources if {
	count(br_messages([br_rc("google_compute_instance", "example", {"name": "test-instance"})])) == 0
}

# The control module's own basic-role grants (infra/terraform/terraform_ci.tf).
test_allows_control_module_basic_role_grants if {
	count(br_messages([
		br_member("ci_terraform_apply_editor", "runnerforge", "roles/editor", br_apply),
		br_member("ci_terraform_plan_viewer", "runnerforge", "roles/viewer", br_plan),
	])) == 0
}

test_allows_apply_editor_in_pool if {
	count(br_messages([br_member("ci_terraform_apply_pool_editor", "runnerforge-pool", "roles/editor", br_apply)])) == 0
}

test_denies_plan_viewer_in_pool if {
	br_flags([br_member("ci_terraform_plan_pool_viewer", "runnerforge-pool", "roles/viewer", br_plan)], "google_project_iam_member.ci_terraform_plan_pool_viewer", "roles/viewer")
}

test_denies_allowlisted_member_with_other_basic_role if {
	br_flags([br_member("apply_owner", "runnerforge", "roles/owner", br_apply)], "google_project_iam_member.apply_owner", "roles/owner")
	br_flags([br_member("plan_editor", "runnerforge", "roles/editor", br_plan)], "google_project_iam_member.plan_editor", "roles/editor")
}

test_denies_basic_role_on_resource_without_project if {
	b := br_rc("google_storage_bucket_iam_member", "state", {"bucket": "runnerforge-terraform-state", "role": "roles/viewer", "member": br_plan})
	br_flags([b], "google_storage_bucket_iam_member.state", "roles/viewer")
}

test_denies_editor_in_binding if {
	b := br_rc("google_project_iam_binding", "b", {"project": "runnerforge", "role": "roles/editor", "members": ["user:someone@example.com"]})
	br_flags([b], "google_project_iam_binding.b", "roles/editor")
}

test_denies_owner_in_policy_data if {
	pd := json.marshal({"bindings": [{"role": "roles/owner", "members": ["user:someone@example.com"]}]})
	p := br_rc("google_project_iam_policy", "p", {"project": "runnerforge", "policy_data": pd})
	br_flags([p], "google_project_iam_policy.p", "roles/owner")
}

test_ignores_member_remove if {
	rm := br_rc("google_project_iam_member_remove", "default_compute_editor", {"project": "runnerforge-pool", "role": "roles/editor", "member": "serviceAccount:355964341703-compute@developer.gserviceaccount.com"})
	count(br_messages([rm])) == 0
}

test_denies_unknown_member_with_editor if {
	br_flags([br_member("unknown", "runnerforge", "roles/editor", null)], "google_project_iam_member.unknown", "roles/editor")
}

test_denies_binding_with_unknown_members_and_editor if {
	b := br_rc("google_project_iam_binding", "b", {"project": "runnerforge", "role": "roles/editor", "members": null})
	br_flags([b], "google_project_iam_binding.b", "roles/editor")
}

test_allows_destroyed_basic_role_grant if {
	gone := {
		"address": "google_project_iam_member.old_owner",
		"mode": "managed",
		"type": "google_project_iam_member",
		"change": {"actions": ["delete"], "after": null},
	}
	count(br_messages([gone])) == 0
}
