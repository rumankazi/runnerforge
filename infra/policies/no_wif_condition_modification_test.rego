package main

import rego.v1

test_denies_attribute_condition_modification if {
	some msg in deny with input as {
		"resource_changes": [{
			"address": "google_iam_workload_identity_pool_provider.github_pool_provider",
			"type": "google_iam_workload_identity_pool_provider",
			"change": {
				"actions": ["update"],
				"before": {"attribute_condition": "assertion.repository_owner == 'rumankazi' && assertion.repository == 'rumankazi/runnerforge'"},
				"after": {"attribute_condition": "assertion.repository_owner == 'rumankazi'"},
			},
		}],
	}
	contains(msg, "attribute_condition")
}

test_denies_attribute_condition_removal if {
	some msg in deny with input as {
		"resource_changes": [{
			"address": "google_iam_workload_identity_pool_provider.github_pool_provider",
			"type": "google_iam_workload_identity_pool_provider",
			"change": {
				"actions": ["update"],
				"before": {"attribute_condition": "assertion.repository == 'rumankazi/runnerforge'"},
				"after": {"attribute_condition": null}
			},
		}],
	}
}

test_allows_update_to_other_fields if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_iam_workload_identity_pool_provider.github_pool_provider",
			"type": "google_iam_workload_identity_pool_provider",
			"change": {
				"actions": ["update"],
				"before": {
					"attribute_condition": "assertion.repository == 'rumankazi/runnerforge'",
					"display_name": "Old name",
				},
				"after": {
					"attribute_condition": "assertion.repository == 'rumankazi/runnerforge'",
					"display_name": "New name",
				},
			},
		}],
	}
}

test_allows_creation_of_new_wif_provider if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_iam_workload_identity_pool_provider.new_provider",
			"type": "google_iam_workload_identity_pool_provider",
			"change": {
				"actions": ["create"],
				"before": null,
				"after": {"attribute_condition": "assertion.repository == 'rumankazi/runnerforge'"},
			},
		}],
	}
}
