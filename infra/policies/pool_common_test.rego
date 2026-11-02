package main

import rego.v1

test_pool_module_repointed_fails if {
	some msg in deny with input as {"variables": {"pool_project_id": {"value": "runnerforge-pool-2"}}, "resource_changes": []}
	contains(msg, "pool_project_id is \"runnerforge-pool-2\"")
}

test_pool_module_project_matches_passes if {
	count(deny) == 0 with input as {"variables": {"pool_project_id": {"value": "runnerforge-pool"}}, "resource_changes": []}
}

test_control_target_without_variable_passes if {
	count(deny) == 0 with input as {"variables": {"project_id": {"value": "runnerforge"}}, "resource_changes": []}
}

test_pool_planned_skips_destroyed_and_data if {
	ids := {rc.address | some rc in pool_planned} with input as {"resource_changes": [
		{"address": "a.kept", "mode": "managed", "type": "a", "change": {"actions": ["no-op"], "after": {"project": "runnerforge-pool"}}},
		{"address": "a.gone", "mode": "managed", "type": "a", "change": {"actions": ["delete"], "after": null}},
		{"address": "data.a.read", "mode": "data", "type": "a", "change": {"actions": ["read"], "after": {"project": "runnerforge-pool"}}},
		{"address": "a.control", "mode": "managed", "type": "a", "change": {"actions": ["create"], "after": {"project": "runnerforge"}}},
	]}
	ids == {"a.kept"}
}
