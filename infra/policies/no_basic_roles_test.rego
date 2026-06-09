package main

import rego.v1

# Should fire when owner is granted
test_denies_owner_role if {
	"Resource \"google_project_iam_member.bad\" grants basic role \"roles/owner\". Use a specific predefined or custom role instead." in deny with input as {
		"resource_changes": [{
			"address": "google_project_iam_member.bad",
			"type": "google_project_iam_member",
			"change": {
				"actions": ["create"],
				"after": {
					"role": "roles/owner",
					"member": "serviceAccount:test@example.iam.gserviceaccount.com",
				},
			},
		}],
	}
}

# Should NOT fire for a specific role
test_allows_specific_role if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_project_iam_member.good",
			"type": "google_project_iam_member",
			"change": {
				"actions": ["create"],
				"after": {
					"role": "roles/run.developer",
					"member": "serviceAccount:test@example.iam.gserviceaccount.com",
				},
			},
		}],
	}
}

# Should NOT fire for non-IAM resources
test_allows_non_iam_resources if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_compute_instance.example",
			"type": "google_compute_instance",
			"change": {"after": {"name": "test-instance"}},
		}],
	}
}

test_allows_basic_role_for_whitelisted_sa if {
	count(deny) == 0 with input as {
		"resource_changes": [{
			"address": "google_project_iam_member.ci_terraform_apply_editor",
			"type": "google_project_iam_member",
			"change": {
				"actions": ["create"],
				"after": {
					"role": "roles/editor",
					"member": "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com",
				},
			},
		}],
	}
}
