package main

import rego.v1

# poolTerraformReader (ADR-020 step 3): what ci-terraform-plan needs to refresh
# infra/terraform-pool, and nothing that reads a VM. An instance's metadata
# holds its runner registration token, so no compute.instances.*,
# compute.disks.* or log-entry read is ever added. The policy's copy of the role
# in infra/terraform-pool/iam.tf: Terraform may grant fewer, never more.
# pool_orchestrator.rego denies anything outside this list.
pool_reader_role_id := "poolTerraformReader"

pool_reader_permissions := {
	"resourcemanager.projects.get",
	"resourcemanager.projects.getIamPolicy",
	"serviceusage.services.get",
	"serviceusage.services.list",
	"iam.serviceAccounts.get",
	"iam.serviceAccounts.list",
	"iam.roles.get",
	"iam.roles.list",
	"compute.projects.get",
	"compute.networks.get",
	"compute.networks.list",
	"compute.subnetworks.get",
	"compute.subnetworks.list",
	"compute.subnetworks.getIamPolicy",
	"compute.firewalls.get",
	"compute.firewalls.list",
	"compute.routers.get",
	"compute.routers.list",
	"compute.addresses.get",
	"compute.addresses.list",
	"compute.routes.get",
	"compute.routes.list",
	"dns.policies.get",
	"dns.policies.list",
	"logging.exclusions.get",
	"logging.exclusions.list",
	"logging.logMetrics.get",
	"logging.logMetrics.list",
	"logging.sinks.get",
	"logging.sinks.list",
	"monitoring.alertPolicies.get",
	"monitoring.alertPolicies.list",
	"monitoring.notificationChannels.get",
	"monitoring.notificationChannels.list",
	"cloudquotas.quotas.get",
}

# What refreshing each type in the pool module reads. Keyed by type for
# managed resources and data.<type> for data sources. A new type in
# infra/terraform-pool fails the pool plan until it's listed here, so the plan
# account never finds out on a drift run that it can't read it.
pool_reader_coverage := {
	"data.google_project": {"resourcemanager.projects.get"},
	"data.google_service_account": {"iam.serviceAccounts.get"},
	"google_project_service": {"resourcemanager.projects.get", "serviceusage.services.list"},
	"google_project_iam_member": {"resourcemanager.projects.getIamPolicy"},
	"google_project_iam_member_remove": {"resourcemanager.projects.getIamPolicy"},
	"google_compute_subnetwork_iam_member": {"compute.subnetworks.getIamPolicy"},
	"google_project_iam_custom_role": {"iam.roles.get"},
	"google_compute_project_metadata": {"compute.projects.get"},
	"google_compute_network": {"compute.networks.get"},
	"google_compute_subnetwork": {"compute.subnetworks.get"},
	"google_compute_firewall": {"compute.firewalls.get"},
	"google_compute_router": {"compute.routers.get"},
	"google_compute_router_nat": {"compute.routers.get"},
	"google_compute_address": {"compute.addresses.get"},
	"google_dns_policy": {"dns.policies.get"},
	"google_logging_metric": {"logging.logMetrics.get"},
	"google_logging_project_exclusion": {"logging.exclusions.get"},
	"google_monitoring_alert_policy": {"monitoring.alertPolicies.get"},
	"google_monitoring_notification_channel": {"monitoring.notificationChannels.get"},
	"google_cloud_quotas_quota_preference": {"cloudquotas.quotas.get"},
	"google_monitoring_monitored_project": {"resourcemanager.projects.get"},
	"google_project_default_service_accounts": set(),
	"time_sleep": set(),
}

pool_config_key(r) := r.type if r.mode == "managed"

pool_config_key(r) := sprintf("data.%s", [r.type]) if r.mode == "data"

pool_config_resources contains r if {
	pool_target
	some r in input.configuration.root_module.resources
}

# The reader role as planned in the pool. A role being destroyed has
# `after: null` and drops out.
pool_reader_roles contains rc if {
	some rc in pool_custom_roles
	pool_field(rc.change.after, "role_id") == pool_reader_role_id
}

pool_reader_planned_permissions := {p |
	some rc in pool_reader_roles
	some p in pool_field(rc.change.after, "permissions")
}

deny contains msg if {
	some r in pool_config_resources
	key := pool_config_key(r)
	not key in object.keys(pool_reader_coverage)
	msg := sprintf(
		"Unmapped type %s (%q) in the pool module: add it to the coverage map (pool_reader_coverage in infra/policies/pool_reader.rego) and its read permissions to poolTerraformReader.",
		[key, r.address],
	)
}

deny contains msg if {
	some r in pool_config_resources
	key := pool_config_key(r)
	some p in pool_reader_coverage[key]
	not p in pool_reader_planned_permissions
	msg := sprintf(
		"poolTerraformReader can't refresh %s: it lacks %q. Add it to the role in infra/terraform-pool/iam.tf and to pool_reader_permissions in infra/policies/pool_reader.rego.",
		[key, p],
	)
}

# A real plan always carries the configuration; unit inputs without one skip this.
deny contains msg if {
	pool_target
	input.configuration.root_module
	count(pool_reader_roles) == 0
	msg := "The pool plan has no poolTerraformReader custom role in project runnerforge-pool. ci-terraform-plan refreshes the pool with it (ADR-020)."
}

# The coverage check reads only the root module's resources.
deny contains msg if {
	pool_target
	calls := object.get(input.configuration, ["root_module", "module_calls"], {})
	count(calls) > 0
	msg := sprintf("The pool module calls modules %v. pool_reader.rego only checks root-module resources; extend it first.", [sort(object.keys(calls))])
}

# ci-terraform-plan holds nothing else in the pool: no roles/viewer or
# roles/iam.securityReviewer (both list instances), and no role on a pool
# resource from another target. roles/monitoring.viewer is the documented
# fallback if the notification-channel reads can't go in the custom role.
pool_plan_member := "serviceAccount:ci-terraform-plan@runnerforge.iam.gserviceaccount.com"

pool_plan_roles := {
	"projects/runnerforge-pool/roles/poolTerraformReader",
	"roles/monitoring.viewer",
}

deny contains msg if {
	some g in iam_grants
	g.member == pool_plan_member
	pool_iam_target(g)
	not pool_plan_grant_allowed(g)
	msg := sprintf(
		"%q grants %v to ci-terraform-plan in the pool. There it holds only %v, on the project (ADR-020).",
		[g.address, g.role, sort(pool_plan_roles)],
	)
}

pool_plan_grant_allowed(g) if {
	g.type in {"google_project_iam_member", "google_project_iam_binding"}
	g.role in pool_plan_roles
}
