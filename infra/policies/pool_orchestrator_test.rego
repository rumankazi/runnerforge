package main

import rego.v1

po_rc(type, name, after) := {
	"address": sprintf("%s.%s", [type, name]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": after, "after_unknown": {}},
}

po_role(name, role_id, permissions) := po_rc("google_project_iam_custom_role", name, {
	"project": "runnerforge-pool",
	"role_id": role_id,
	"permissions": permissions,
})

po_orchestrator_role(extra) := po_role("orchestrator_pool", "runnerforgeOrchestratorPool", array.concat(sort(pool_orchestrator_permissions), extra))

po_subnet_role(extra) := po_role("pool_subnet_user", "runnerforgePoolSubnetUser", array.concat(["compute.subnetworks.use"], extra))

po_reader_role(extra) := po_role("terraform_reader", "poolTerraformReader", array.concat(sort(pool_reader_permissions), extra))

po_member(name, role, member) := po_rc("google_project_iam_member", name, {"project": "runnerforge-pool", "role": role, "member": member})

po_subnet_member(subnetwork, region) := po_rc("google_compute_subnetwork_iam_member", "orchestrator_pool_subnet", {
	"project": "runnerforge-pool",
	"region": region,
	"subnetwork": subnetwork,
	"role": "projects/runnerforge-pool/roles/runnerforgePoolSubnetUser",
	"member": pool_orchestrator_member,
})

# Everything step 3a declares in infra/terraform-pool/iam.tf.
po_intended := [
	po_orchestrator_role([]),
	po_subnet_role([]),
	po_reader_role([]),
	po_member("orchestrator_pool_vms", "projects/runnerforge-pool/roles/runnerforgeOrchestratorPool", pool_orchestrator_member),
	po_member("orchestrator_pool_logging_viewer", "roles/logging.viewer", pool_orchestrator_member),
	po_subnet_member("runnerforge-pool-europe-west4", "europe-west4"),
	po_member("ci_terraform_plan_pool_reader", "projects/runnerforge-pool/roles/poolTerraformReader", "serviceAccount:ci-terraform-plan@runnerforge.iam.gserviceaccount.com"),
	po_member("ci_terraform_apply_pool_editor", "roles/editor", "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"),
	po_member("ci_terraform_apply_pool_security_admin", "roles/iam.securityAdmin", "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"),
	po_member("ci_terraform_apply_pool_role_admin", "roles/iam.roleAdmin", "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"),
	po_member("ci_terraform_apply_logging_config_writer", "roles/logging.configWriter", "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"),
]

po_messages(changes) := m if {
	m := deny with input as {"variables": un_pool_vars, "resource_changes": changes}
}

po_has(changes, fragment) if {
	some msg in po_messages(changes)
	contains(msg, fragment)
}

# po_intended with the resource at `address` replaced.
po_with(address, rc) := [x |
	some c in po_intended
	x := po_pick(c, address, rc)
]

po_pick(c, address, rc) := rc if c.address == address

po_pick(c, address, _) := c if c.address != address

test_pool_iam_intended_set_passes if {
	count(po_messages(po_intended)) == 0
}

test_orchestrator_role_extra_permission_fails if {
	changes := po_with("google_project_iam_custom_role.orchestrator_pool", po_orchestrator_role(["compute.subnetworks.useExternalIp"]))
	po_has(changes, `adds ["compute.subnetworks.useExternalIp"]`)
}

test_orchestrator_role_with_subnet_use_fails if {
	changes := po_with("google_project_iam_custom_role.orchestrator_pool", po_orchestrator_role(["compute.subnetworks.use"]))
	po_has(changes, `(runnerforgeOrchestratorPool) adds ["compute.subnetworks.use"]`)
}

test_subnet_role_extra_permission_fails if {
	changes := po_with("google_project_iam_custom_role.pool_subnet_user", po_subnet_role(["compute.subnetworks.useExternalIp"]))
	po_has(changes, `(runnerforgePoolSubnetUser) adds ["compute.subnetworks.useExternalIp"]`)
}

test_fewer_permissions_pass if {
	changes := po_with("google_project_iam_custom_role.orchestrator_pool", po_role("orchestrator_pool", "runnerforgeOrchestratorPool", ["compute.instances.get"]))
	count(po_messages(changes)) == 0
}

test_other_custom_role_in_pool_fails if {
	po_has([po_role("extra", "poolSomethingElse", ["compute.instances.get"])], `has role_id "poolSomethingElse"`)
}

test_unknown_role_id_fails if {
	r := po_rc("google_project_iam_custom_role", "x", {"project": "runnerforge-pool", "permissions": ["compute.instances.get"]})
	po_has([r], "has a role_id unknown at plan time")
}

test_unknown_permissions_fail if {
	whole := po_rc("google_project_iam_custom_role", "orchestrator_pool", {"project": "runnerforge-pool", "role_id": "runnerforgeOrchestratorPool", "permissions": null})
	po_has([whole], "has permissions unknown at plan time")

	element := object.union(po_orchestrator_role([]), {"change": {"after_unknown": {"permissions": [false, true]}}})
	po_has([element], "has permissions unknown at plan time")
}

test_custom_role_in_control_project_is_ignored if {
	r := object.union(po_role("ci_firestore_database_creator", "firestoreDatabaseCreator", ["datastore.databases.create"]), {"change": {"after": {"project": "runnerforge"}}})
	count(deny) == 0 with input as {"resource_changes": [r]}
}

test_subnet_role_at_project_level_fails if {
	po_has(
		[po_member("subnet_everywhere", "projects/runnerforge-pool/roles/runnerforgePoolSubnetUser", pool_orchestrator_member)],
		"Grant it only on the subnetwork",
	)
	po_has(
		[po_member("subnet_everywhere", "projects/runnerforge-pool/roles/runnerforgePoolSubnetUser", "serviceAccount:other@runnerforge.iam.gserviceaccount.com")],
		"Grant it only on the subnetwork",
	)
}

test_orchestrator_instance_admin_in_pool_fails if {
	po_has(
		[po_member("orchestrator_instance_admin", "roles/compute.instanceAdmin.v1", pool_orchestrator_member)],
		"grants roles/compute.instanceAdmin.v1 to the orchestrator in the pool",
	)
}

test_orchestrator_unknown_role_fails if {
	po_has([po_member("orchestrator_pool_vms", null, pool_orchestrator_member)], "grants null to the orchestrator in the pool")
}

test_orchestrator_subnet_by_self_link_passes if {
	sl := "https://www.googleapis.com/compute/v1/projects/runnerforge-pool/regions/europe-west4/subnetworks/runnerforge-pool-europe-west4"
	count(po_messages([po_subnet_member(sl, null)])) == 0
	count(po_messages([po_subnet_member("projects/runnerforge-pool/regions/europe-west4/subnetworks/runnerforge-pool-europe-west4", null)])) == 0
}

test_orchestrator_other_subnet_fails if {
	po_has([po_subnet_member("runnerforge-pool-us-central1", "us-central1")], "to the orchestrator in the pool")
	po_has([po_subnet_member("runnerforge-pool-europe-west4", "us-central1")], "to the orchestrator in the pool")
	po_has([po_subnet_member("projects/runnerforge/regions/europe-west4/subnetworks/runnerforge-pool-europe-west4", null)], "to the orchestrator in the pool")
	po_has([po_subnet_member(null, "europe-west4")], "to the orchestrator in the pool")
}

test_orchestrator_subnet_role_via_binding_fails if {
	b := po_rc("google_compute_subnetwork_iam_binding", "s", {
		"project": "runnerforge-pool",
		"region": "europe-west4",
		"subnetwork": "runnerforge-pool-europe-west4",
		"role": "projects/runnerforge-pool/roles/runnerforgePoolSubnetUser",
		"members": [pool_orchestrator_member],
	})
	po_has([b], "to the orchestrator in the pool")
}

test_orchestrator_in_control_project_is_ignored if {
	m := object.union(po_member("orchestrator_instance_admin", "roles/compute.instanceAdmin.v1", pool_orchestrator_member), {"change": {"after": {"project": "runnerforge"}}})
	count(deny) == 0 with input as {"resource_changes": [m]}
}

po_sa_grant(name, sa_id, role) := {
	"address": sprintf("google_service_account_iam_member.%s", [name]),
	"mode": "managed",
	"type": "google_service_account_iam_member",
	"change": {"actions": ["create"], "after": {"service_account_id": sa_id, "role": role, "member": pool_orchestrator_member}},
}

po_control_has(changes, fragment) if {
	some msg in deny with input as {"variables": {"image_tag": {"value": "x"}}, "resource_changes": changes}
	contains(msg, fragment)
}

test_orchestrator_on_pool_sa_from_control_fails if {
	pool_sa := "projects/runnerforge-pool/serviceAccounts/355964341703-compute@developer.gserviceaccount.com"
	po_control_has([po_sa_grant("token", pool_sa, "roles/iam.serviceAccountTokenCreator")], "grants roles/iam.serviceAccountTokenCreator to the orchestrator in the pool")
	po_control_has([po_sa_grant("act_as", pool_sa, "roles/iam.serviceAccountUser")], "grants roles/iam.serviceAccountUser to the orchestrator in the pool")
	po_control_has(
		[po_sa_grant("by_email", "projects/-/serviceAccounts/x@runnerforge-pool.iam.gserviceaccount.com", "roles/iam.serviceAccountUser")],
		"grants roles/iam.serviceAccountUser to the orchestrator in the pool",
	)
	po_control_has(
		[po_sa_grant("by_number", "projects/355964341703/serviceAccounts/123456789", "roles/iam.serviceAccountUser")],
		"grants roles/iam.serviceAccountUser to the orchestrator in the pool",
	)
}

test_orchestrator_acts_as_control_runner_vm_passes if {
	g := po_sa_grant("orchestrator_acts_as_runnerforge_runner_vm", "projects/runnerforge/serviceAccounts/runnerforge-runner-vm@runnerforge.iam.gserviceaccount.com", "roles/iam.serviceAccountUser")
	count(deny) == 0 with input as {"variables": {"image_tag": {"value": "x"}}, "resource_changes": [g]}
}

test_orchestrator_upper_case_member_fails if {
	m := po_member("admin", "roles/compute.admin", "serviceAccount:Runnerforge-Orchestrator@runnerforge.iam.gserviceaccount.com")
	po_has([m], "grants roles/compute.admin to the orchestrator in the pool")
}
