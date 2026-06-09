package main

import rego.v1

# WIF (Workload Identity Federation) attribute_condition is the provider-level
# gate that determines which workflows can authenticate to GCP at all. It's the
# top of the trust chain — per-SA workflow_path pinning is the fine-grained
# defense beneath it. Modifying this condition could broaden the auth surface
# (allow other repos, remove repo restrictions, etc.).
#
# Tightening is also blocked by this policy — if you need a legitimate
# refactor, do it via a deliberate two-PR procedure:
#   (1) remove the relevant attribute_mapping or refactor with no condition change
#   (2) modify the condition in a follow-up PR with explicit review
#
# This is intentional friction. The condition is small, doesn't need to change
# often, and changes to it deserve maximum review attention.

deny contains msg if {
	some i
	rc := input.resource_changes[i]
	rc.type == "google_iam_workload_identity_pool_provider"
	"update" in rc.change.actions
	rc.change.before.attribute_condition != rc.change.after.attribute_condition
	msg := sprintf(
		"WIF provider %q is modifying attribute_condition. Before: %q. After: %q. Any change to this condition is blocked by policy — see infra/policies/no_wif_condition_modification.rego.",
		[rc.address, rc.change.before.attribute_condition, rc.change.after.attribute_condition],
	)
}
