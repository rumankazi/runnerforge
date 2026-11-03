# IAM in the pool project (ADR-020 step 3). Members are literal emails and role
# names are spelled out from role_id (known at plan time, unlike .name), so the
# control root never has to read anything here. infra/policies/pool_orchestrator.rego
# and pool_reader.rego hold the policy's copy of each custom role's permissions:
# change both together.

locals {
  ci_terraform_apply = "serviceAccount:ci-terraform-apply@runnerforge.iam.gserviceaccount.com"
  ci_terraform_plan  = "serviceAccount:ci-terraform-plan@runnerforge.iam.gserviceaccount.com"
  orchestrator       = "serviceAccount:runnerforge-orchestrator@runnerforge.iam.gserviceaccount.com"
}

# ------- Terraform CI -------

# The step-0 grants, made by hand when the project was created (an apply can't
# grant itself a new class of permission), codified here for drift detection.
# Re-declaring an existing binding is a no-op. Editor stays for now:
# securityAdmin already lets this account grant itself anything.
#trivy:ignore:GCP-0007 -- the Terraform apply account, granted in step 0; this only codifies it
resource "google_project_iam_member" "ci_terraform_apply_pool_editor" {
  project = var.pool_project_id
  role    = "roles/editor"
  member  = local.ci_terraform_apply

  lifecycle {
    prevent_destroy = true
  }
}

#trivy:ignore:GCP-0007 -- the Terraform apply account, granted in step 0; this only codifies it
resource "google_project_iam_member" "ci_terraform_apply_pool_security_admin" {
  project = var.pool_project_id
  role    = "roles/iam.securityAdmin"
  member  = local.ci_terraform_apply

  lifecycle {
    prevent_destroy = true
  }
}

#trivy:ignore:GCP-0007 -- the Terraform apply account, granted in step 0; this only codifies it
resource "google_project_iam_member" "ci_terraform_apply_pool_role_admin" {
  project = var.pool_project_id
  role    = "roles/iam.roleAdmin"
  member  = local.ci_terraform_apply

  lifecycle {
    prevent_destroy = true
  }
}

# Granted by hand before the first apply that needed it, then codified.
# roles/editor can only read log exclusions; logging.tf creates one.
resource "google_project_iam_member" "ci_terraform_apply_logging_config_writer" {
  project = var.pool_project_id
  role    = "roles/logging.configWriter"
  member  = local.ci_terraform_apply

  lifecycle {
    prevent_destroy = true
  }
}

# What the plan account needs to refresh this module, and nothing that reads a
# VM: roles/viewer and roles/iam.securityReviewer both list instances, and an
# instance's metadata holds its runner registration token. It replaced those two
# hand grants (removed below). pool_reader.rego checks that every resource type
# here is readable with it.
resource "google_project_iam_custom_role" "terraform_reader" {
  project     = var.pool_project_id
  role_id     = "poolTerraformReader"
  title       = "RunnerForge pool Terraform reader"
  description = "Refreshes infra/terraform-pool for plans and drift detection. Never add compute.instances.*, compute.disks.* or log-entry reads (ADR-020)."
  permissions = [
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
  ]

  # Only a role_id or project change replaces it; editing permissions doesn't.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_iam_member" "ci_terraform_plan_pool_reader" {
  project = var.pool_project_id
  role    = "projects/${var.pool_project_id}/roles/${google_project_iam_custom_role.terraform_reader.role_id}"
  member  = local.ci_terraform_plan

  depends_on = [google_project_iam_custom_role.terraform_reader]

  lifecycle {
    prevent_destroy = true
  }
}

# ------- Orchestrator -------

# Creates, lists, labels and deletes runner VMs with no service account and no
# external IP. Nothing here can add an access config, a service account, a
# network interface, a firewall rule or a route; audit.tf alerts if anyone
# does. The runner image stays in the control project, read through the
# orchestrator's roles/compute.imageUser there.
resource "google_project_iam_custom_role" "orchestrator_pool" {
  project     = var.pool_project_id
  role_id     = "runnerforgeOrchestratorPool"
  title       = "RunnerForge orchestrator (pool VMs)"
  description = "Creates, lists, labels and deletes no-SA, no-external-IP runner VMs (ADR-020). Never add network, firewall, route, address, access-config or IAM permissions."
  permissions = [
    "compute.instances.create",
    "compute.disks.create",
    "compute.disks.setLabels",
    "compute.instances.setLabels",
    # Insert-time metadata, and step 4 turning serial logging off once a job starts.
    "compute.instances.setMetadata",
    "compute.instances.get",
    "compute.instances.list",
    "compute.instances.delete",
    "compute.zoneOperations.get",
    "compute.machineTypes.list",
    # The step-7 egress kill reads each VM's network byte counters.
    "monitoring.timeSeries.list",
  ]
}

resource "google_project_iam_member" "orchestrator_pool_vms" {
  project = var.pool_project_id
  role    = "projects/${var.pool_project_id}/roles/${google_project_iam_custom_role.orchestrator_pool.role_id}"
  member  = local.orchestrator

  depends_on = [google_project_iam_custom_role.orchestrator_pool]
}

# Granted on the one subnet, not the project, so a subnet added later can't be
# used until it's granted explicitly.
resource "google_project_iam_custom_role" "pool_subnet_user" {
  project     = var.pool_project_id
  role_id     = "runnerforgePoolSubnetUser"
  title       = "RunnerForge pool subnet user"
  description = "Attach VMs to one subnet. Grant only on a subnetwork, never on the project (ADR-020)."
  permissions = ["compute.subnetworks.use"]
}

resource "google_compute_subnetwork_iam_member" "orchestrator_pool_subnet" {
  project    = var.pool_project_id
  region     = google_compute_subnetwork.pool.region
  subnetwork = google_compute_subnetwork.pool.name
  role       = "projects/${var.pool_project_id}/roles/${google_project_iam_custom_role.pool_subnet_user.role_id}"
  member     = local.orchestrator

  depends_on = [google_project_iam_custom_role.pool_subnet_user]
}

# The preemption check reads the pool's audit log (ADR-020; also reads
# tenant-written serial and DNS logs, which the ADR accepts).
resource "google_project_iam_member" "orchestrator_pool_logging_viewer" {
  project = var.pool_project_id
  role    = "roles/logging.viewer"
  member  = local.orchestrator
}

# ------- Grants removed (ADR-020 step 3b) -------

# Grants made outside Terraform that must stay gone. A _member_remove resource
# removes its grant when created and stays in state while the grant is absent.
# If the grant comes back, the next refresh drops it from state, so the next
# plan shows it to add (drift), and applying a plan made after that removes the
# grant again (tfaction applies the PR's saved plan). Deleting one of these
# blocks only stops that check; it never restores a grant.
#
# So re-granting roles/viewer is no fix for a plan that can't read something:
# add the missing permission to poolTerraformReader by hand instead, and make
# the PR that codifies it the next pool merge (terraform-drift runbook).
#
# _member_remove rewrites the project policy without the provider's IAM lock or
# its retry on a concurrent change, so each one waits for every other write to
# the project policy here (enabling an API can add a service agent to it), and
# for the removal before it. Add any new one (an IAM member or binding, an API)
# to the first removal's depends_on.

# Replaced by poolTerraformReader.
resource "google_project_iam_member_remove" "ci_terraform_plan_pool_viewer" {
  project = var.pool_project_id
  role    = "roles/viewer"
  member  = local.ci_terraform_plan

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.ci_terraform_apply_pool_editor,
    google_project_iam_member.ci_terraform_apply_pool_security_admin,
    google_project_iam_member.ci_terraform_apply_pool_role_admin,
    google_project_iam_member.ci_terraform_apply_logging_config_writer,
    google_project_iam_member.ci_terraform_plan_pool_reader,
    google_project_iam_member.orchestrator_pool_vms,
    google_project_iam_member.orchestrator_pool_logging_viewer,
  ]
}

# Replaced by poolTerraformReader.
resource "google_project_iam_member_remove" "ci_terraform_plan_pool_security_reviewer" {
  project = var.pool_project_id
  role    = "roles/iam.securityReviewer"
  member  = local.ci_terraform_plan

  depends_on = [google_project_iam_member_remove.ci_terraform_plan_pool_viewer]
}

# Granted by Google when Compute was enabled. The account is disabled
# (service_accounts.tf); this keeps Editor off it if it's ever re-enabled.
resource "google_project_iam_member_remove" "default_compute_pool_editor" {
  project = var.pool_project_id
  role    = "roles/editor"
  member  = "serviceAccount:${data.google_project.pool.number}-compute@developer.gserviceaccount.com"

  depends_on = [google_project_iam_member_remove.ci_terraform_plan_pool_security_reviewer]
}
