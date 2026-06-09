package main

import rego.v1

test_denies_destroy_of_protected_resource if {
	some msg in deny with input as {
		"resource_changes": [{
			"address": "google_storage_bucket.terraform_state",
			"type": "google_storage_bucket",
			"change": {"actions": ["delete"]},
		}],
	}
	contains(msg, "protected-destroy allowlist")
}

test_denies_replace_of_protected_resource if {
	some msg in deny with input as {
		"resource_changes": [{
			"address": "google_iam_workload_identity_pool.github_pool",
			"type": "google_iam_workload_identity_pool",
			"change": {"actions": ["delete", "create"]},
		}],
	}
	contains(msg, "protected-destroy allowlist")
}

test_allows_destroy_of_unprotected_resource if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_compute_instance.disposable",
			"type": "google_compute_instance",
			"change": {"actions": ["delete"]},
		}],
	}
}

test_allows_update_of_protected_resource if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_cloud_run_v2_service.orchestrator",
			"type": "google_cloud_run_v2_service",
			"change": {"actions": ["update"]},
		}],
	}
}
