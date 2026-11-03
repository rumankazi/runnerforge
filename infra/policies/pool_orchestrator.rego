package main

import rego.v1

# The pool's custom roles and the orchestrator's access to the pool (ADR-020
# step 3). The permission lists are the policy's copy of the roles in
# infra/terraform-pool/iam.tf: Terraform may grant fewer, never more. Change
# both together. The orchestrator creates VMs with no service account and no
# external IP, so nothing here may add a network, firewall, route, address,
# access-config, service-account or IAM permission.

pool_orchestrator_member := "serviceAccount:runnerforge-orchestrator@runnerforge.iam.gserviceaccount.com"

pool_orchestrator_role := "projects/runnerforge-pool/roles/runnerforgeOrchestratorPool"

pool_subnet_user_role := "projects/runnerforge-pool/roles/runnerforgePoolSubnetUser"

pool_region := "europe-west4"

pool_subnet_name := "runnerforge-pool-europe-west4"

pool_orchestrator_permissions := {
	"compute.instances.create",
	"compute.disks.create",
	"compute.disks.setLabels",
	"compute.instances.setLabels",
	"compute.instances.setMetadata",
	"compute.instances.get",
	"compute.instances.list",
	"compute.instances.delete",
	"compute.zoneOperations.get",
	"compute.machineTypes.list",
	"monitoring.timeSeries.list",
}

pool_subnet_user_permissions := {"compute.subnetworks.use"}

# The only custom roles in the pool, by role_id. poolTerraformReader's list is
# in pool_reader.rego.
pool_custom_role_permissions := {
	"runnerforgeOrchestratorPool": pool_orchestrator_permissions,
	"runnerforgePoolSubnetUser": pool_subnet_user_permissions,
	"poolTerraformReader": pool_reader_permissions,
}

pool_custom_role_files := {
	"runnerforgeOrchestratorPool": "pool_orchestrator.rego",
	"runnerforgePoolSubnetUser": "pool_orchestrator.rego",
	"poolTerraformReader": "pool_reader.rego",
}

pool_custom_roles contains rc if {
	some rc in pool_planned
	rc.type == "google_project_iam_custom_role"
}

deny contains msg if {
	some rc in pool_custom_roles
	not is_string(pool_field(rc.change.after, "role_id"))
	msg := sprintf("Pool custom role %q has a role_id unknown at plan time. Spell it out so the pool policies can check it.", [rc.address])
}

deny contains msg if {
	some rc in pool_custom_roles
	rid := pool_field(rc.change.after, "role_id")
	is_string(rid)
	not rid in object.keys(pool_custom_role_permissions)
	msg := sprintf(
		"Pool custom role %q has role_id %q. The pool has only %v (ADR-020); add a new one to pool_custom_role_permissions in infra/policies/pool_orchestrator.rego first.",
		[rc.address, rid, sort(object.keys(pool_custom_role_permissions))],
	)
}

deny contains msg if {
	some rc in pool_custom_roles
	pool_permissions_unknown(rc.change)
	msg := sprintf("Pool custom role %q has permissions unknown at plan time. List them literally so the pool policies can check them.", [rc.address])
}

pool_permissions_unknown(change) if not is_array(pool_field(change.after, "permissions"))

pool_permissions_unknown(change) if object.get(change, ["after_unknown", "permissions"], false) == true

pool_permissions_unknown(change) if {
	u := object.get(change, ["after_unknown", "permissions"], [])
	is_array(u)
	some x in u
	x == true
}

deny contains msg if {
	some rc in pool_custom_roles
	rid := pool_field(rc.change.after, "role_id")
	allowed := pool_custom_role_permissions[rid]
	perms := pool_field(rc.change.after, "permissions")
	is_array(perms)
	extra := {p | some p in perms; not p in allowed}
	count(extra) > 0
	msg := sprintf(
		"Pool custom role %q (%s) adds %v, which the policy's copy in infra/policies/%s doesn't list (ADR-020). Change both together, after review.",
		[rc.address, rid, sort(extra), pool_custom_role_files[rid]],
	)
}

# Everything the orchestrator holds in the pool, including on pool resources
# granted from another target.
pool_orchestrator_grants contains g if {
	some g in iam_grants
	g.member == pool_orchestrator_member
	pool_iam_target(g)
}

deny contains msg if {
	some g in pool_orchestrator_grants
	not pool_orchestrator_grant_allowed(g)
	msg := sprintf(
		"%q grants %v to the orchestrator in the pool. There it holds only %q and %q on the project, and %q on subnetwork %s (ADR-020).",
		[g.address, g.role, pool_orchestrator_role, "roles/logging.viewer", pool_subnet_user_role, pool_subnet_name],
	)
}

pool_project_iam_types := {"google_project_iam_member", "google_project_iam_binding", "google_project_iam_policy"}

pool_orchestrator_grant_allowed(g) if {
	g.type in pool_project_iam_types
	g.role in {pool_orchestrator_role, "roles/logging.viewer"}
}

pool_orchestrator_grant_allowed(g) if {
	g.type == "google_compute_subnetwork_iam_member"
	g.role == pool_subnet_user_role
	pool_is_pool_subnet(iam_grant_after(g))
}

pool_is_pool_subnet(a) if {
	pool_field(a, "subnetwork") == pool_subnet_name
	pool_field(a, "region") == pool_region
}

# An id or self_link: the project and region are in the path.
pool_is_pool_subnet(a) if {
	s := pool_field(a, "subnetwork")
	is_string(s)
	regex.match(`(^|/)projects/runnerforge-pool/regions/europe-west4/subnetworks/runnerforge-pool-europe-west4$`, s)
}

# On the project, the subnet role would cover every subnet, including one
# added later. It is granted per subnetwork only.
deny contains msg if {
	some g in iam_grants
	is_string(g.role)
	endswith(g.role, "/roles/runnerforgePoolSubnetUser")
	not startswith(g.type, "google_compute_subnetwork_iam_")
	msg := sprintf("%q grants %q to %v on a %s. Grant it only on the subnetwork (ADR-020).", [g.address, g.role, g.member, g.type])
}
