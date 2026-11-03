package main

import rego.v1

pr_change(type, name, actions, after) := {
	"address": sprintf("%s.%s", [type, name]),
	"mode": "managed",
	"type": type,
	"change": {"actions": actions, "after": after},
}

pr_member(project, member) := pr_change("google_project_iam_member", "m", ["create"], {"project": project, "role": "roles/logging.logWriter", "member": member})

pr_messages(changes) := m if {
	m := deny with input as {"resource_changes": changes}
}

pr_flags(changes, member) if {
	some msg in pr_messages(changes)
	contains(msg, sprintf("to pool principal %q", [member]))
}

test_pool_sa_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")], "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")
}

test_pool_default_compute_sa_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:355964341703-compute@developer.gserviceaccount.com")], "serviceAccount:355964341703-compute@developer.gserviceaccount.com")
}

test_pool_service_agent_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:service-355964341703@compute-system.iam.gserviceaccount.com")], "serviceAccount:service-355964341703@compute-system.iam.gserviceaccount.com")
}

test_pool_cloudservices_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:355964341703@cloudservices.gserviceaccount.com")], "serviceAccount:355964341703@cloudservices.gserviceaccount.com")
}

test_pool_appspot_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:runnerforge-pool@appspot.gserviceaccount.com")], "serviceAccount:runnerforge-pool@appspot.gserviceaccount.com")
}

test_pool_wif_principal_in_control_fails if {
	m := "principalSet://iam.googleapis.com/projects/355964341703/locations/global/workloadIdentityPools/p/*"
	pr_flags([pr_member("runnerforge", m)], m)
}

test_pool_convenience_member_fails if {
	b := pr_change("google_storage_bucket_iam_member", "state", ["create"], {"bucket": "runnerforge-terraform-state", "role": "roles/storage.objectViewer", "member": "projectViewer:runnerforge-pool"})
	pr_flags([b], "projectViewer:runnerforge-pool")
}

test_pool_sa_on_control_resource_without_project_fails if {
	sa := pr_change("google_service_account_iam_member", "token", ["create"], {"service_account_id": "projects/runnerforge/serviceAccounts/orchestrator@runnerforge.iam.gserviceaccount.com", "role": "roles/iam.serviceAccountTokenCreator", "member": "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com"})
	pr_flags([sa], "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")
}

test_pool_sa_in_binding_fails if {
	b := pr_change("google_project_iam_binding", "b", ["update"], {"project": "runnerforge", "role": "roles/viewer", "members": ["serviceAccount:orchestrator@runnerforge.iam.gserviceaccount.com", "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com"]})
	pr_flags([b], "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")
}

test_pool_sa_in_policy_fails if {
	pd := json.marshal({"bindings": [{"role": "roles/viewer", "members": ["serviceAccount:service-355964341703@compute-system.iam.gserviceaccount.com"]}]})
	p := pr_change("google_project_iam_policy", "p", ["create"], {"project": "runnerforge", "policy_data": pd})
	pr_flags([p], "serviceAccount:service-355964341703@compute-system.iam.gserviceaccount.com")
}

test_pool_principal_in_pool_passes if {
	count(pr_messages([pr_member("runnerforge-pool", "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")])) == 0
}

test_control_principal_in_pool_passes if {
	# Step 3: the orchestrator's custom role in the pool.
	count(pr_messages([pr_member("runnerforge-pool", "serviceAccount:orchestrator@runnerforge.iam.gserviceaccount.com")])) == 0
}

test_control_sa_named_after_pool_passes if {
	# Step 8's unlink function lives in the control project; its name may mention the pool.
	count(pr_messages([pr_member("runnerforge", "serviceAccount:runnerforge-pool-unlink@runnerforge.iam.gserviceaccount.com")])) == 0
}

test_removed_grant_passes if {
	gone := pr_change("google_project_iam_member", "m", ["delete"], null)
	count(pr_messages([gone])) == 0
}

test_member_remove_resource_ignored if {
	rm := pr_change("google_project_iam_member_remove", "r", ["create"], {"project": "runnerforge", "role": "roles/editor", "member": "serviceAccount:355964341703-compute@developer.gserviceaccount.com"})
	count(pr_messages([rm])) == 0
}

test_live_policy_with_pool_principal_fails if {
	pd := json.marshal({"bindings": [{"role": "roles/viewer", "members": ["serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com"]}]})
	some msg in deny with input as {
		"resource_changes": [],
		"prior_state": {"values": {"root_module": {"resources": [{
			"address": "data.google_project_iam_policy.control",
			"mode": "data",
			"type": "google_project_iam_policy",
			"values": {"project": "runnerforge", "policy_data": pd},
		}]}}},
	}
	contains(msg, "The live IAM policy of project \"runnerforge\"")
}

test_live_policy_without_pool_principal_passes if {
	pd := json.marshal({"bindings": [{"role": "roles/viewer", "members": ["serviceAccount:orchestrator@runnerforge.iam.gserviceaccount.com"]}]})
	count(deny) == 0 with input as {
		"resource_changes": [],
		"prior_state": {"values": {"root_module": {"resources": [{
			"address": "data.google_project_iam_policy.control",
			"mode": "data",
			"type": "google_project_iam_policy",
			"values": {"project": "runnerforge", "policy_data": pd},
		}]}}},
	}
}

# Not a basic role: no_basic_roles.rego denies one with an unknown member.
test_unknown_binding_member_does_not_error if {
	b := pr_change("google_project_iam_binding", "b", ["create"], {"project": "runnerforge", "role": "roles/logging.viewer", "members": [null, "user:owner@example.com"]})
	count(pr_messages([b])) == 0
}

test_pool_principal_upper_case_in_control_fails if {
	pr_flags([pr_member("runnerforge", "serviceAccount:X@RunnerForge-Pool.iam.gserviceaccount.com")], "serviceAccount:x@runnerforge-pool.iam.gserviceaccount.com")
}

test_live_policy_upper_case_pool_principal_fails if {
	pd := json.marshal({"bindings": [{"role": "roles/viewer", "members": ["serviceAccount:X@RUNNERFORGE-POOL.iam.gserviceaccount.com"]}]})
	some msg in deny with input as {
		"resource_changes": [],
		"prior_state": {"values": {"root_module": {"resources": [{
			"address": "data.google_project_iam_policy.control",
			"mode": "data",
			"type": "google_project_iam_policy",
			"values": {"project": "runnerforge", "policy_data": pd},
		}]}}},
	}
	contains(msg, "The live IAM policy of project \"runnerforge\"")
}
