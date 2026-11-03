package main

import rego.v1

ss_has(i, fragment) if {
	some msg in deny with input as i
	contains(msg, fragment)
}

test_secret_resource_changes_fail_in_any_target if {
	every t in ["google_secret_manager_secret_version", "google_service_account_key", "random_password", "tls_private_key", "google_compute_ssl_certificate"] {
		rc := {"address": sprintf("module.x.%s.k", [t]), "mode": "managed", "type": t, "change": {"actions": ["create"], "after": {}}}
		ss_has({"resource_changes": [rc]}, sprintf("%q (%s) would keep a secret in Terraform state", [rc.address, t]))
		ss_has({"variables": un_pool_vars, "resource_changes": [rc]}, "would keep a secret in Terraform state")
	}
}

# A data source the plan reads has no resource change, only configuration.
test_secret_data_source_in_configuration_fails if {
	i := cr_input(array.concat(cr_config, [rd_config("data", "google_secret_manager_secret_version", "key")]), [cr_reader([])])
	ss_has(i, `"data.google_secret_manager_secret_version.key" (data.google_secret_manager_secret_version) would keep a secret`)
}

# The secret's metadata and IAM hold no secret.
test_secret_metadata_passes if {
	count(cr_messages(cr_config, [cr_reader([])])) == 0
}

ss_job(target) := {
	"address": "google_cloud_scheduler_job.sweep",
	"mode": "managed",
	"type": "google_cloud_scheduler_job",
	"change": {"actions": ["update"], "after": object.union({"http_target": [], "app_engine_http_target": []}, target)},
}

# The shape a real plan has for the two OIDC jobs.
test_scheduler_job_without_headers_or_body_passes if {
	job := ss_job({"http_target": [{"body": "", "headers": {}, "http_method": "POST", "oidc_token": [{"audience": "x"}]}]})
	count(deny) == 0 with input as {"resource_changes": [job]}
}

test_scheduler_job_header_or_body_fails if {
	ss_has({"resource_changes": [ss_job({"http_target": [{"headers": {"Authorization": "Bearer x"}}]})]}, "sets http_target.headers")
	ss_has({"resource_changes": [ss_job({"http_target": [{"body": "eyJ0b2tlbiI6IngifQ=="}]})]}, "sets http_target.body")
	ss_has({"resource_changes": [ss_job({"app_engine_http_target": [{"headers": {"X-Key": "x"}}]})]}, "sets app_engine_http_target.headers")
}

ss_channel(sensitive) := {
	"address": "google_monitoring_notification_channel.slack",
	"mode": "managed",
	"type": "google_monitoring_notification_channel",
	"change": {"actions": ["create"], "after": {"type": "slack", "sensitive_labels": sensitive}},
}

test_notification_channel_sensitive_labels_fail if {
	ss_has({"resource_changes": [ss_channel([{"auth_token": "xoxb-x"}])]}, "sets sensitive_labels")
}

# The shape a real plan has for the owner's email channel.
test_notification_channel_without_sensitive_labels_passes if {
	count(deny) == 0 with input as {"resource_changes": [ss_channel([])]}
}
