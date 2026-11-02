package main

import rego.v1

un_rc(type, after, unknown) := {
	"address": sprintf("%s.x", [type]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": after, "after_unknown": unknown},
}

un_pool_vars := {"pool_project_id": {"value": "runnerforge-pool"}}

un_messages(vars, changes) := m if {
	m := deny with input as {"variables": vars, "resource_changes": changes}
}

un_has(vars, changes, fragment) if {
	some msg in un_messages(vars, changes)
	contains(msg, fragment)
}

test_pool_target_unknown_project_fails if {
	nat_excl := un_rc("google_logging_project_exclusion", {"filter": `LOG_ID("compute.googleapis.com/nat_flows")`}, {"project": true})
	un_has(un_pool_vars, [nat_excl], "has a project unknown at plan time")
}

test_exclusion_or_sink_without_project_fails_in_any_target if {
	un_has({}, [un_rc("google_logging_project_exclusion", {"filter": "x"}, {"project": true})], "must set project explicitly")
	un_has({}, [un_rc("google_logging_project_sink", {"destination": "x"}, {"project": true})], "must set project explicitly")
}

test_pool_target_known_project_passes if {
	excl := un_rc("google_logging_project_exclusion", {"project": "runnerforge-pool", "filter": `resource.type="dns_query" AND sample(insertId, 0.99)`}, {})
	count(un_messages(un_pool_vars, [excl])) == 0
}

test_pool_target_iam_outside_pool_fails if {
	m := un_rc("google_project_iam_member", {"project": "runnerforge", "role": "roles/viewer"}, {"member": true})
	un_has(un_pool_vars, [m], "grants IAM outside the pool project")
}

test_pool_target_iam_inside_pool_passes if {
	m := un_rc("google_project_iam_member", {"project": "runnerforge-pool", "role": "roles/logging.configWriter", "member": "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"}, {})
	count(un_messages(un_pool_vars, [m])) == 0
}

test_control_target_pool_service_account_fails if {
	sa := un_rc("google_service_account", {"project": "runnerforge-pool", "account_id": "x"}, {})
	un_has({"project_id": {"value": "runnerforge"}}, [sa], "declares a pool service account outside the pool target")
}
