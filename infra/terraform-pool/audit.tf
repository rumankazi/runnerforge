# Alerts on the pool's Admin Activity audit log (ADR-020 step 3). That log is
# always on, free and can't be excluded. Drift detection only sees resources in
# Terraform state; these see changes to anything, made by anyone. Filters are
# heredocs so "\." reaches Logging as a single escaped dot.

locals {
  pool_audit_runbook = "https://github.com/rumankazi/runnerforge/blob/main/apps/web/content/docs/guides/runbooks/pool-audit.mdx"

  pool_audit_labels = {
    method    = "EXTRACT(protoPayload.methodName)"
    principal = "EXTRACT(protoPayload.authenticationInfo.principalEmail)"
    resource  = "EXTRACT(protoPayload.resourceName)"
  }
}

# Any change not made by Terraform's apply account, or by the orchestrator doing
# its job (creating and deleting VMs, and from step 4 turning serial logging
# off). Change the orchestrator's methods together with its role in iam.tf.
resource "google_monitoring_alert_policy" "pool_out_of_band" {
  project      = var.pool_project_id
  display_name = "Pool changed outside Terraform"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "Admin Activity entry by an unexpected principal"
    condition_matched_log {
      filter           = <<-EOT
        LOG_ID("cloudaudit.googleapis.com/activity")
        NOT protoPayload.authenticationInfo.principalEmail=("ci-terraform-apply@runnerforge.iam.gserviceaccount.com" OR "service-agent-manager@system.gserviceaccount.com")
        NOT protoPayload.methodName=~"\.GetOperation$"
        NOT (protoPayload.authenticationInfo.principalEmail="runnerforge-orchestrator@runnerforge.iam.gserviceaccount.com" AND protoPayload.methodName=~"^v1\.compute\.instances\.(insert|delete|setMetadata)$")
        NOT (operation.last=true AND NOT operation.first=true)
      EOT
      label_extractors = local.pool_audit_labels
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    content   = "Something in the pool project was changed outside Terraform. Runbook: ${local.pool_audit_runbook}"
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.owner_email.name]
}

# Any path that would let a VM bypass the NAT, reach another network or get
# credentials: an external IP, a service account, another network interface, a
# peering, VPN or interconnect, or login and startup metadata on a VM or the
# project, by anyone, Terraform included (so a reviewed change to metadata.tf
# fires it too). Keyed on the permissions checked, not the request shape.
# setMetadata keeps only the items it sends, so a VM losing a key the
# orchestrator sets at insert, or its startup script, fires it too (step 4).
# setCommonInstanceMetadata checks iam.serviceAccounts.actAs for any caller,
# hence that exception; its metadata keys are matched separately.
resource "google_monitoring_alert_policy" "pool_external_path" {
  project      = var.pool_project_id
  display_name = "Pool VM path out of the sandbox"
  combiner     = "OR"
  severity     = "CRITICAL"

  conditions {
    display_name = "External IP, service account, interface, peering or login metadata"
    condition_matched_log {
      filter           = <<-EOT
        LOG_ID("cloudaudit.googleapis.com/activity")
        (
          protoPayload.authorizationInfo.permission=("compute.subnetworks.useExternalIp" OR "compute.networks.useExternalIp" OR "compute.instances.addAccessConfig" OR "compute.instances.updateAccessConfig" OR "compute.addresses.use" OR "compute.instances.setServiceAccount" OR "compute.instances.addNetworkInterface" OR "compute.instances.updateNetworkInterface" OR "compute.networks.addPeering" OR "compute.vpnTunnels.create" OR "compute.vpnGateways.create" OR "compute.targetVpnGateways.create" OR "compute.interconnectAttachments.create" OR "networkconnectivity.spokes.create")
          OR (protoPayload.authorizationInfo.permission="iam.serviceAccounts.actAs" AND NOT protoPayload.methodName=~"\.compute\.projects\.setCommonInstanceMetadata$")
          OR protoPayload.methodName=~"^(v1|beta|alpha)\.compute\.instances\.(addAccessConfig|updateAccessConfig|addNetworkInterface|updateNetworkInterface|setServiceAccount)$"
          OR (protoPayload.methodName=~"\.compute\.instances\.setMetadata$" AND protoPayload.metadata.instanceMetadataDelta.addedMetadataKeys=("serial-port-enable" OR "ssh-keys" OR "sshKeys" OR "enable-oslogin" OR "block-project-ssh-keys" OR "startup-script" OR "startup-script-url" OR "shutdown-script" OR "shutdown-script-url" OR "enable-guest-attributes"))
          OR (protoPayload.methodName=~"\.compute\.projects\.setCommonInstanceMetadata$" AND (protoPayload.metadata.projectMetadataDelta.addedMetadataKeys=("serial-port-enable" OR "ssh-keys" OR "sshKeys" OR "enable-oslogin" OR "block-project-ssh-keys" OR "startup-script" OR "startup-script-url" OR "shutdown-script" OR "shutdown-script-url" OR "enable-guest-attributes") OR protoPayload.metadata.projectMetadataDelta.modifiedMetadataKeys=("serial-port-enable" OR "ssh-keys" OR "sshKeys" OR "enable-oslogin" OR "block-project-ssh-keys" OR "startup-script" OR "startup-script-url" OR "shutdown-script" OR "shutdown-script-url" OR "enable-guest-attributes") OR protoPayload.metadata.projectMetadataDelta.deletedMetadataKeys=("enable-oslogin" OR "serial-port-logging-enable")))
          OR (protoPayload.methodName=~"\.compute\.instances\.setMetadata$" AND protoPayload.metadata.instanceMetadataDelta.modifiedMetadataKeys=("serial-port-enable" OR "ssh-keys" OR "sshKeys" OR "enable-oslogin" OR "block-project-ssh-keys" OR "startup-script" OR "startup-script-url" OR "shutdown-script" OR "shutdown-script-url" OR "enable-guest-attributes"))
          OR (protoPayload.methodName=~"\.compute\.instances\.setMetadata$" AND protoPayload.metadata.instanceMetadataDelta.deletedMetadataKeys=("enable-oslogin" OR "block-project-ssh-keys" OR "serial-port-logging-enable" OR "startup-script"))
        )
      EOT
      label_extractors = local.pool_audit_labels
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    content   = "A change in the pool project could let a VM bypass the NAT or get credentials. Runbook: ${local.pool_audit_runbook}"
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.owner_email.name]
}
