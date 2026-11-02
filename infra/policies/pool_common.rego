package main

import rego.v1

# Shared by the pool policies (ADR-020). Every conftest run loads all of
# infra/policies/ against each tfaction target's plan, so the pool rules pick
# their resources by the project they land in, not by which target is planned.
# That also catches a pool resource declared in the control module.

pool_project_id := "runnerforge-pool"

pool_project_number := "355964341703"

# The policy's own copy of internal_ranges in infra/terraform-pool/variables.tf,
# so shrinking that list in Terraform fails here instead of passing silently.
# Terraform may deny more; this is the floor. Change both together.
pool_internal_ranges := {
	"10.0.0.0/8",
	"172.16.0.0/12",
	"192.168.0.0/16",
	"100.64.0.0/10",
	"169.254.0.0/16",
	"192.0.0.0/24",
	"192.0.2.0/24",
	"192.88.99.0/24",
	"198.18.0.0/15",
	"198.51.100.0/24",
	"203.0.113.0/24",
	"240.0.0.0/4",
}

# The pool's hard ceilings (ADR-020 step 2), the policy's copy of
# local.pool_quota_caps in infra/terraform-pool/quotas.tf. Any other quota id
# in the pool is capped at 0. Change both together.
pool_quota_caps := {
	"INSTANCES-per-project-region": 10,
	"N2-CPUS-per-project-region": 32,
	"CPUS-ALL-REGIONS-per-project": 32,
	"SSD-TOTAL-GB-per-project-region": 500,
	"DISKS-TOTAL-GB-per-project-region": 200,
}

# Managed resources in the pool project as they will be after the apply.
# A resource being destroyed has `after: null` and drops out, so a deleted deny
# rule never counts as coverage. Unchanged resources are listed as "no-op".
pool_planned contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	is_object(rc.change.after)
	rc.change.after.project == pool_project_id
}

pool_empty(x) if x == null

pool_empty(x) if {
	x != null
	count(x) == 0
}

pool_field(obj, key) := object.get(obj, key, null)

# Repointing the pool module at another project would make every pool rule
# match nothing. The variable only exists in the pool target's plan.
deny contains msg if {
	v := input.variables.pool_project_id.value
	v != pool_project_id
	msg := sprintf(
		"pool_project_id is %q, but the pool policies check project %q. Update pool_project_id in infra/policies/pool_common.rego in the same PR.",
		[v, pool_project_id],
	)
}
