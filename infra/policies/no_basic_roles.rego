package main

import rego.v1

# Basic (primitive) roles grant far more than what's typically needed.
# Force the use of specific predefined or custom roles by failing the plan
# if any grant (a member, a binding or a policy, on any resource) uses one.

basic_roles := {
	"roles/owner",
	"roles/editor",
	"roles/viewer",
}

# The only basic-role grants allowed, as exact (member, role, project) tuples.
# Adding to this list requires PR + CODEOWNERS review — adds a real friction
# for new exceptions. Existing exceptions reflect S2 design decisions:
#   - ci-terraform-apply needs editor-equivalent breadth across many resource
#     types TF manages, in the control project and the pool (ADR-020).
#     Granular role enumeration would mean ongoing maintenance every time TF
#     starts managing a new resource type.
# Per-SA WIF principal pinning + CODEOWNERS on /infra/terraform/ + this OPA
# policy together are the layered defenses; the basic role grant is acceptable
# given the surrounding constraints.
#
# ci-terraform-plan holds no basic role: roles/viewer lists instances, and an
# instance's metadata holds its runner registration token. It reads each project
# through a custom role instead (ADR-020 steps 3 and 3f; pool_reader.rego and
# control_reader.rego).
basic_role_allowlist := {
	{
		"member": "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com",
		"role": "roles/editor",
		"project": "runnerforge",
	},
	{
		"member": "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com",
		"role": "roles/editor",
		"project": "runnerforge-pool",
	},
}

# A member unknown at plan time is null, so it never matches the allowlist.
deny contains msg if {
	some g in iam_grants
	g.role in basic_roles
	not {"member": g.member, "role": g.role, "project": g.project} in basic_role_allowlist
	msg := sprintf(
		"Resource %q grants basic role %q to %v in project %v. Use a specific predefined or custom role instead (allowed exceptions: basic_role_allowlist in policies/no_basic_roles.rego).",
		[g.address, g.role, g.member, g.project],
	)
}
