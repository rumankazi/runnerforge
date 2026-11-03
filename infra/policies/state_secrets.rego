package main

import rego.v1

# The repo is public, and every plan run uploads its plan, prior state
# included, as a workflow artifact anyone signed in to GitHub can download for
# 90 days. So neither root keeps a secret in state: secrets are added to Secret
# Manager by hand (ADR-020 step 3f). Keyed like pool_reader_coverage.
state_secret_types := {
	"google_secret_manager_secret_version",
	"google_secret_manager_regional_secret_version",
	"google_service_account_key",
	"google_apikeys_key",
	"google_sql_user",
	"google_sql_ssl_cert",
	"google_compute_ssl_certificate",
	"google_compute_region_ssl_certificate",
	"google_storage_hmac_key",
	"google_kms_secret_ciphertext",
	"random_password",
	"tls_private_key",
	"data.google_secret_manager_secret_version",
	"data.google_secret_manager_secret_version_access",
	"data.google_secret_manager_regional_secret_version",
	"data.google_secret_manager_regional_secret_version_access",
	"data.google_kms_secret",
	"data.google_service_account_access_token",
	"data.google_service_account_id_token",
	"data.google_client_config",
}

# Root-module configuration covers data sources, which a plan reads without a
# resource change; resource_changes covers modules.
state_secret_resources contains r if {
	some r in input.configuration.root_module.resources
	pool_config_key(r) in state_secret_types
}

state_secret_resources contains rc if {
	some rc in input.resource_changes
	pool_config_key(rc) in state_secret_types
}

deny contains msg if {
	some r in state_secret_resources
	msg := sprintf("%q (%s) would keep a secret in Terraform state, which every plan run publishes as an artifact of this public repo. Add the secret by hand instead (ADR-020).", [r.address, pool_config_key(r)])
}

# A Cloud Scheduler job keeps its request headers and body in state. The jobs
# authenticate with an OIDC token, which isn't stored.
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_cloud_scheduler_job"
	is_object(rc.change.after)
	some kind in ["http_target", "app_engine_http_target"]
	some t in pool_field(rc.change.after, kind)
	some field in ["headers", "body"]
	not pool_empty(pool_field(t, field))
	msg := sprintf("%q sets %s.%s, which Terraform state keeps and every plan run publishes. Use oidc_token, never a secret header or body (ADR-020).", [rc.address, kind, field])
}

# A Slack or PagerDuty channel keeps its token in sensitive_labels. The owner's
# email channel has none.
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_monitoring_notification_channel"
	is_object(rc.change.after)
	not pool_empty(pool_field(rc.change.after, "sensitive_labels"))
	msg := sprintf("%q sets sensitive_labels, which Terraform state keeps and every plan run publishes. Add the channel by hand instead (ADR-020).", [rc.address])
}
