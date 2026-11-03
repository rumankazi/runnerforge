package main

import rego.v1

# controlTerraformReader (ADR-020 step 3f): what ci-terraform-plan needs to
# refresh infra/terraform, plus the pool root's reads in this project, and
# nothing that reads a VM, a log entry, a Firestore document, a secret version
# or an object. The plan account can be impersonated from any branch, and an
# instance's metadata holds its runner registration token. The policy's copy of
# the role in infra/terraform/terraform_ci.tf: Terraform may grant fewer, never
# more. Change both together.
control_project_id := "runnerforge"

control_reader_role_id := "controlTerraformReader"

control_reader_permissions := {
	"artifactregistry.repositories.get",
	"artifactregistry.repositories.getIamPolicy",
	"cloudquotas.quotas.get",
	"cloudscheduler.jobs.fullView",
	"cloudscheduler.jobs.get",
	"compute.routers.get",
	"compute.subnetworks.get",
	"datastore.databases.getMetadata",
	"datastore.schemas.get",
	"iam.googleapis.com/workloadIdentityPoolProviders.get",
	"iam.googleapis.com/workloadIdentityPools.get",
	"iam.googleapis.com/workloadIdentityPools.getAttestationRules",
	"iam.roles.get",
	"iam.serviceAccounts.get",
	"iam.serviceAccounts.getIamPolicy",
	"logging.logMetrics.get",
	"monitoring.alertPolicies.get",
	"monitoring.dashboards.get",
	"monitoring.notificationChannels.get",
	"monitoring.services.get",
	"monitoring.slos.get",
	"resourcemanager.projects.get",
	"resourcemanager.projects.getIamPolicy",
	"run.services.get",
	"run.services.getIamPolicy",
	"secretmanager.secrets.get",
	"secretmanager.secrets.getIamPolicy",
	"serviceusage.services.list",
	"storage.buckets.get",
	"storage.buckets.getIamPolicy",
}

# What refreshing each type in the control module reads, keyed like
# pool_reader_coverage. A new type in infra/terraform fails the control plan
# until it's listed here.
control_reader_coverage := {
	"data.google_project": {"resourcemanager.projects.get"},
	"data.google_service_account": {"iam.serviceAccounts.get"},
	"data.google_compute_subnetwork": {"compute.subnetworks.get"},
	"google_project_service": {"resourcemanager.projects.get", "serviceusage.services.list"},
	"google_project_iam_member": {"resourcemanager.projects.getIamPolicy"},
	"google_project_iam_member_remove": {"resourcemanager.projects.getIamPolicy"},
	"google_project_iam_custom_role": {"iam.roles.get"},
	"google_service_account": {"iam.serviceAccounts.get"},
	"google_service_account_iam_member": {"iam.serviceAccounts.getIamPolicy"},
	"google_iam_workload_identity_pool": {
		"iam.googleapis.com/workloadIdentityPools.get",
		"iam.googleapis.com/workloadIdentityPools.getAttestationRules",
	},
	"google_iam_workload_identity_pool_provider": {"iam.googleapis.com/workloadIdentityPoolProviders.get"},
	"google_storage_bucket": {"storage.buckets.get"},
	"google_storage_bucket_iam_member": {"storage.buckets.getIamPolicy"},
	"google_artifact_registry_repository": {"artifactregistry.repositories.get"},
	"google_artifact_registry_repository_iam_member": {"artifactregistry.repositories.getIamPolicy"},
	"google_cloud_run_v2_service": {"run.services.get"},
	"google_cloud_run_v2_service_iam_member": {"run.services.getIamPolicy"},
	"google_cloud_scheduler_job": {"cloudscheduler.jobs.get", "cloudscheduler.jobs.fullView"},
	"google_secret_manager_secret": {"secretmanager.secrets.get"},
	"google_secret_manager_secret_iam_member": {"secretmanager.secrets.getIamPolicy"},
	"google_firestore_database": {"datastore.databases.getMetadata"},
	"google_firestore_index": {"datastore.schemas.get"},
	"google_firestore_field": {"datastore.schemas.get"},
	"google_compute_router": {"compute.routers.get"},
	"google_compute_router_nat": {"compute.routers.get"},
	"google_logging_metric": {"logging.logMetrics.get"},
	"google_monitoring_alert_policy": {"monitoring.alertPolicies.get"},
	"google_monitoring_slo": {"monitoring.slos.get"},
	"google_monitoring_service": {"monitoring.services.get"},
	"google_monitoring_notification_channel": {"monitoring.notificationChannels.get"},
	"google_monitoring_dashboard": {"monitoring.dashboards.get"},
	"google_cloud_quotas_quota_preference": {"cloudquotas.quotas.get"},
	"time_sleep": set(),
}

# Reads another target makes in this project, which no control type maps.
control_reader_external_needs := {"resourcemanager.projects.get": "google_monitoring_monitored_project.control_scope in infra/terraform-pool reads this project's metrics scope"}

# Every permission names a read of settings: a get, a list or an IAM policy.
# The forbidden list catches the gets that return data.
control_reader_allowed := `\.(get|getIamPolicy|getMetadata|list|fullView|getAttestationRules)$`

control_reader_forbidden := [
	`^compute\.(instances|disks|regionDisks|snapshots|machineImages|instanceTemplates|regionInstanceTemplates|instanceSettings)\.`,
	`^compute\.projects\.get$`,
	`^compute\.images\.useReadOnly$`,
	`^logging\.(logEntries|privateLogEntries|views)\.`,
	`^logging\.buckets\.copyLogEntries$`,
	`^datastore\.entities\.`,
	`^datastore\.databases\.get$`,
	`^secretmanager\.versions\.`,
	`^storage\.(objects|hmacKeys|managedFolders)\.`,
	`^artifactregistry\.(files\.|repositories\.(downloadArtifacts|readViaVirtualRepository|exportArtifacts)$)`,
	`^apikeys\.keys\.getKeyString$`,
	`^(cloudtrace|cloudbuild|bigquery|errorreporting)\.`,
	`^cloudtasks\.tasks\.`,
	`^monitoring\.timeSeries\.`,
	`^run\.[A-Za-z]+\.sshRead$`,
	`^iam\.serviceAccounts\.(getAccessToken|signBlob|signJwt|actAs|implicitDelegation|getOpenIdToken)$`,
]

# The control target is the plan whose default provider names this project
# literally. The pool's provider takes var.pool_project_id, which can't be
# runnerforge.
control_target if {
	input.configuration.provider_config.google.expressions.project.constant_value == control_project_id
}

# Every policy picks its rules by target, so a plan that matches neither would
# skip the coverage checks. A real plan always carries the configuration; unit
# inputs without one skip this.
deny contains msg if {
	input.configuration.root_module
	not control_target
	not pool_target
	msg := "This plan matches neither tfaction target (control: the google provider's project is \"runnerforge\"; pool: var.pool_project_id is set). Update control_target in infra/policies/control_reader.rego or pool_target in pool_unknowns.rego."
}

deny contains msg if {
	control_target
	pool_target
	msg := "This plan matches both the control and the pool target. Fix control_target in infra/policies/control_reader.rego."
}

control_config_resources contains r if {
	control_target
	some r in input.configuration.root_module.resources
}

# Custom roles planned in this project, in any target. A role being destroyed
# has `after: null` and drops out.
control_custom_roles contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_project_iam_custom_role"
	is_object(rc.change.after)
	pool_field(rc.change.after, "project") == control_project_id
}

control_reader_roles contains rc if {
	some rc in control_custom_roles
	pool_field(rc.change.after, "role_id") == control_reader_role_id
}

control_reader_planned_permissions := {p |
	some rc in control_reader_roles
	some p in pool_field(rc.change.after, "permissions")
}

deny contains msg if {
	some r in control_config_resources
	key := pool_config_key(r)
	not key in object.keys(control_reader_coverage)
	msg := sprintf(
		"Unmapped type %s (%q) in the control module: add it to the coverage map (control_reader_coverage in infra/policies/control_reader.rego) and its read permissions to controlTerraformReader.",
		[key, r.address],
	)
}

deny contains msg if {
	some r in control_config_resources
	key := pool_config_key(r)
	some p in control_reader_coverage[key]
	not p in control_reader_planned_permissions
	msg := sprintf(
		"controlTerraformReader can't refresh %s: it lacks %q. Add it to the role in infra/terraform/terraform_ci.tf and to control_reader_permissions in infra/policies/control_reader.rego.",
		[key, p],
	)
}

deny contains msg if {
	control_target
	some p, why in control_reader_external_needs
	not p in control_reader_planned_permissions
	msg := sprintf("controlTerraformReader lacks %q, which %s. Keep it in the role.", [p, why])
}

# A real plan always carries the configuration; unit inputs without one skip this.
deny contains msg if {
	control_target
	input.configuration.root_module
	count(control_reader_roles) == 0
	msg := "The control plan has no controlTerraformReader custom role in project runnerforge. ci-terraform-plan refreshes the control module with it (ADR-020)."
}

# The coverage check reads only the root module's resources.
deny contains msg if {
	control_target
	calls := object.get(input.configuration, ["root_module", "module_calls"], {})
	count(calls) > 0
	msg := sprintf("The control module calls modules %v. control_reader.rego only checks root-module resources; extend it first.", [sort(object.keys(calls))])
}

# Every custom role the control target plans outside the pool must have its
# project, role_id and permissions known, or the checks above can't find it.
deny contains msg if {
	control_target
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_project_iam_custom_role"
	is_object(rc.change.after)
	pool_field(rc.change.after, "project") != pool_project_id
	control_role_unknown(rc.change)
	msg := sprintf("Custom role %q has a project, role_id or permissions unknown at plan time. Spell them out so control_reader.rego can check it.", [rc.address])
}

control_role_unknown(change) if not is_string(pool_field(change.after, "project"))

control_role_unknown(change) if not is_string(pool_field(change.after, "role_id"))

control_role_unknown(change) if pool_permissions_unknown(change)

# The pool target never declares a role in this project.
deny contains msg if {
	pool_target
	some rc in control_custom_roles
	msg := sprintf("%q declares a custom role in project runnerforge from the pool target (ADR-020).", [rc.address])
}

deny contains msg if {
	some rc in control_reader_roles
	perms := pool_field(rc.change.after, "permissions")
	is_array(perms)
	extra := {p | some p in perms; not p in control_reader_permissions}
	count(extra) > 0
	msg := sprintf(
		"Custom role %q (controlTerraformReader) adds %v, which the policy's copy in infra/policies/control_reader.rego doesn't list (ADR-020). Change both together, after review.",
		[rc.address, sort(extra)],
	)
}

control_reader_bad(p) if not regex.match(control_reader_allowed, p)

control_reader_bad(p) if {
	some f in control_reader_forbidden
	regex.match(f, p)
}

# Also checks the policy's own copy, so editing both lists together still fails.
deny contains msg if {
	some p in control_reader_permissions
	control_reader_bad(p)
	msg := sprintf("control_reader_permissions lists %q. controlTerraformReader reads settings only, never a VM, a log entry, a document, a secret version or an object (ADR-020).", [p])
}

deny contains msg if {
	some rc in control_reader_roles
	some p in pool_field(rc.change.after, "permissions")
	control_reader_bad(p)
	msg := sprintf("Custom role %q (controlTerraformReader) adds %q. It reads settings only, never a VM, a log entry, a document, a secret version or an object (ADR-020).", [rc.address, p])
}

# ci-terraform-plan's broad control grants, replaced by controlTerraformReader.
# The owner removes them by hand once the role is applied (ADR-020 step 3f).
# From then on a plan that would grant them again fails, so nothing re-grants
# them before the PR that deletes their blocks: an apply runs the PR's saved plan.
control_plan_broad_roles := {"roles/viewer", "roles/iam.securityReviewer"}

deny contains msg if {
	some g in iam_grants
	g.member == pool_plan_member
	g.type in pool_project_iam_types
	not pool_iam_target(g)
	g.role in control_plan_broad_roles
	some rc in input.resource_changes
	rc.address == g.address
	some action in rc.change.actions
	action in {"create", "update"}
	msg := sprintf(
		"%q would grant %s to ci-terraform-plan in runnerforge. It reads the control project through controlTerraformReader only (ADR-020 step 3f); add a missing permission to that role instead (terraform-drift runbook).",
		[g.address, g.role],
	)
}
