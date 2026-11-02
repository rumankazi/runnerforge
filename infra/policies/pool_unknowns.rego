package main

import rego.v1

pool_target if input.variables.pool_project_id

# A project Terraform can't know at plan time (google_logging_project_exclusion
# and _sink have no DefaultProviderProject) would hide a resource from
# pool_planned. In the pool target, every resource that has a project must know it.
deny contains msg if {
	pool_target
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.change.after_unknown.project == true
	msg := sprintf("%q has a project unknown at plan time. Set project = var.pool_project_id explicitly so the pool policies can check it.", [rc.address])
}

# Same hole in any target for the log-routing types the pool rules reason about.
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in {"google_logging_project_exclusion", "google_logging_project_sink"}
	is_object(rc.change.after)

	# object.get, not a bare ref: OPA hoists a bare ref out of `not`, so an
	# absent project would make the whole rule undefined instead of true.
	not is_string(object.get(rc.change.after, "project", null))
	msg := sprintf("%q must set project explicitly (it is unknown at plan time otherwise).", [rc.address])
}

# The pool target never grants IAM outside the pool, so a grant whose member is
# unknown at plan (a pool service account created in the same plan) can't leak.
deny contains msg if {
	pool_target
	some rc in input.resource_changes
	rc.mode == "managed"
	regex.match(`^google_.+_iam_(member|binding|policy)$`, rc.type)
	is_object(rc.change.after)
	object.get(rc.change.after, "project", null) != pool_project_id
	msg := sprintf("%q grants IAM outside the pool project from the pool target (ADR-020).", [rc.address])
}

# Pool service accounts are only declared in the pool target.
deny contains msg if {
	not pool_target
	some rc in pool_planned
	rc.type == "google_service_account"
	msg := sprintf("%q declares a pool service account outside the pool target.", [rc.address])
}
