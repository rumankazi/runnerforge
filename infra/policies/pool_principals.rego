package main

import rego.v1

# No pool principal holds a role outside the pool project (ADR-020). A tenant
# job runs inside the pool, so anything a pool identity can do in the control
# project is a path from tenant code to the orchestrator's data.
#
# Pool principals: service accounts of the pool project (user-managed, the
# default compute and App Engine accounts, Google service agents named by the
# project number), workload identity principals of the pool's pools, and the
# projectOwner/Editor/Viewer convenience members.

# A member unknown at plan time is null here; pool_unknowns.rego covers that.
pool_principal(m) if {
	is_string(m)
	pool_principal_name(m)
}

pool_principal_name(m) if endswith(m, "@runnerforge-pool.iam.gserviceaccount.com")

pool_principal_name(m) if endswith(m, "runnerforge-pool@appspot.gserviceaccount.com")

pool_principal_name(m) if contains(m, pool_project_number)

pool_principal_name(m) if regex.match(`^project(Owner|Editor|Viewer):runnerforge-pool$`, m)

# Every (member, role) a planned IAM resource grants. A grant outside the pool
# project, or on a resource with no `project` field (a bucket, a service
# account), counts as outside: that is conservative, and the pool target has
# no such grants today.
pool_iam_grants contains g if {
	some rc in input.resource_changes
	rc.mode == "managed"
	regex.match(`^google_.+_iam_member$`, rc.type)
	a := rc.change.after
	is_object(a)
	g := {"address": rc.address, "project": pool_field(a, "project"), "role": a.role, "member": a.member}
}

pool_iam_grants contains g if {
	some rc in input.resource_changes
	rc.mode == "managed"
	regex.match(`^google_.+_iam_binding$`, rc.type)
	a := rc.change.after
	is_object(a)
	some m in a.members
	g := {"address": rc.address, "project": pool_field(a, "project"), "role": a.role, "member": m}
}

pool_iam_grants contains g if {
	some rc in input.resource_changes
	rc.mode == "managed"
	regex.match(`^google_.+_iam_policy$`, rc.type)
	a := rc.change.after
	is_object(a)
	is_string(a.policy_data)
	some b in json.unmarshal(a.policy_data).bindings
	some m in b.members
	g := {"address": rc.address, "project": pool_field(a, "project"), "role": b.role, "member": m}
}

deny contains msg if {
	some g in pool_iam_grants
	pool_principal(g.member)
	g.project != pool_project_id
	msg := sprintf(
		"%q grants %q to pool principal %q outside the pool project (project %v). Pool identities hold no role in the control project (ADR-020).",
		[g.address, g.role, g.member, g.project],
	)
}

# Optional live check: if a target reads its project's IAM policy with
# data "google_project_iam_policy", the plan carries the live policy in
# prior_state, so grants made by hand are caught too.
deny contains msg if {
	some r in input.prior_state.values.root_module.resources
	r.mode == "data"
	r.type == "google_project_iam_policy"
	r.values.project != pool_project_id
	some b in json.unmarshal(r.values.policy_data).bindings
	some m in b.members
	pool_principal(m)
	msg := sprintf(
		"The live IAM policy of project %q (%q) grants %q to pool principal %q. Remove it by hand; pool identities hold no role in the control project (ADR-020).",
		[r.values.project, r.address, b.role, m],
	)
}
