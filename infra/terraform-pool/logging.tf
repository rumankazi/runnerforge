# Tenant-driven log volume (ADR-020 step 2). Tenants control how much the NAT,
# firewall, DNS and serial logs write, and NAT and firewall logs are billed when
# generated, whether or not Logging stores them. So: NAT and firewall logs stay
# whole (excluding saves at most half and loses abuse attribution), DNS is
# sampled, and every source is counted per VM and alerted on. The step-7 kill
# acts on these counters; until it exists only the owner's jobs run here.

# Drops 99% of DNS query entries before storage. Log-based metrics count
# excluded entries too, so pool/dns_query_entries still sees every query.
resource "google_logging_project_exclusion" "dns_sample" {
  project     = var.pool_project_id
  name        = "pool-dns-queries-sample"
  description = "Stores 1% of DNS query logs; the pool/dns_query_entries counter still sees all (ADR-020)."
  filter      = "resource.type=\"dns_query\" AND sample(insertId, 0.99)"

  depends_on = [google_project_iam_member.ci_terraform_apply_logging_config_writer]
}

locals {
  pool_log_counters = {
    nat_flow_entries = {
      filter = "resource.type=\"nat_gateway\" AND LOG_ID(\"compute.googleapis.com/nat_flows\")"
      vm     = "EXTRACT(jsonPayload.endpoint.vm_name)"
    }
    firewall_deny_entries = {
      filter = "resource.type=\"gce_subnetwork\" AND LOG_ID(\"compute.googleapis.com/firewall\") AND jsonPayload.disposition=\"DENIED\""
      vm     = "EXTRACT(jsonPayload.instance.vm_name)"
    }
    dns_query_entries = {
      filter = "resource.type=\"dns_query\""
      vm     = "EXTRACT(jsonPayload.vmInstanceName)"
    }
  }
}

resource "google_logging_metric" "pool_vm" {
  for_each = local.pool_log_counters

  project = var.pool_project_id
  name    = "pool/${each.key}"
  filter  = each.value.filter

  # Changing labels forces a replacement (see infra/terraform/observability_metrics.tf).
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "vm_name"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "vm_name" = each.value.vm
  }
}

# The control project's vm_preemption_count, defined again here: log-based
# metrics are per project, and the preemption audit entries land in the project
# that owns the VM. Same name and descriptor, so the Orchestrator Health chart
# sums both through the metrics scope. No alert reads it.
resource "google_logging_metric" "vm_preemption_count" {
  project = var.pool_project_id
  name    = "vm_preemption_count"
  filter  = <<-EOT
    LOG_ID("cloudaudit.googleapis.com/system_event")
    resource.type="gce_instance"
    protoPayload.methodName="compute.instances.preempted"
    protoPayload.resourceName:"instances/runnerforge-"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "zone"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "zone" = "EXTRACT(resource.labels.zone)"
  }
}

# Serial entries already carry the VM as resource.labels.instance_id.
resource "google_logging_metric" "pool_serial_entries" {
  project = var.pool_project_id
  name    = "pool/serial_entries"
  filter  = "resource.type=\"gce_instance\" AND LOG_ID(\"serialconsole.googleapis.com/serial_port_1_output\")"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# The alert API rejects a log-based metric that hasn't reached the metric
# registry yet. Keyed on the names so a new counter re-runs the wait.
resource "time_sleep" "wait_for_pool_log_metrics" {
  create_duration = "120s"

  triggers = {
    log_metrics = join(",", concat(
      [for metric in google_logging_metric.pool_vm : metric.name],
      [google_logging_metric.pool_serial_entries.name],
    ))
  }
}

resource "google_monitoring_notification_channel" "owner_email" {
  project      = var.pool_project_id
  display_name = "RunnerForge pool alerts"
  type         = "email"
  labels = {
    email_address = "kaziruman@gmail.com"
  }
  force_delete = false
}

locals {
  pool_log_volume_runbook = "https://github.com/rumankazi/runnerforge/blob/main/apps/web/content/docs/guides/runbooks/pool-log-volume.mdx"

  # Placeholder thresholds (entries per second, per VM), to be tuned from the
  # step-6 isolation probe. A normal job denies ~0 internal connections.
  pool_log_alerts = {
    firewall_deny = {
      name      = "Pool VM internal-deny log > 5/s (5m)"
      metric    = google_logging_metric.pool_vm["firewall_deny_entries"].name
      resource  = "gce_subnetwork"
      group_by  = "metric.label.vm_name"
      threshold = 5
      duration  = "300s"
    }
    nat = {
      name      = "Pool VM NAT log > 50/s (10m)"
      metric    = google_logging_metric.pool_vm["nat_flow_entries"].name
      resource  = "nat_gateway"
      group_by  = "metric.label.vm_name"
      threshold = 50
      duration  = "600s"
    }
    dns = {
      name      = "Pool VM DNS queries > 100/s (10m)"
      metric    = google_logging_metric.pool_vm["dns_query_entries"].name
      resource  = "dns_query"
      group_by  = "metric.label.vm_name"
      threshold = 100
      duration  = "600s"
    }
    serial = {
      name      = "Pool VM serial log > 50/s (10m)"
      metric    = google_logging_metric.pool_serial_entries.name
      resource  = "gce_instance"
      group_by  = "resource.label.instance_id"
      threshold = 50
      duration  = "600s"
    }
  }
}

resource "google_monitoring_alert_policy" "pool_log_volume" {
  for_each = local.pool_log_alerts

  project      = var.pool_project_id
  display_name = each.value.name
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = each.value.name
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${each.value.metric}\" resource.type=\"${each.value.resource}\""
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.threshold
      duration        = each.value.duration

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = [each.value.group_by]
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "A pool VM is writing tenant-driven logs fast; NAT and firewall logs are billed as they are generated. Runbook: ${local.pool_log_volume_runbook}"
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.owner_email.name]
  depends_on            = [time_sleep.wait_for_pool_log_metrics]
}

# Everything the pool stores in Logging, whatever the source: ~100 KB/s is
# ~8.6 GiB/day. NAT and firewall bytes are billed from the first byte; DNS and
# serial bytes after the project's free 50 GiB a month.
resource "google_monitoring_alert_policy" "pool_log_ingestion" {
  project      = var.pool_project_id
  display_name = "Pool log ingestion > 100 KB/s (30m)"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "Pool bytes ingested > 100 KB/s"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/billing/bytes_ingested\" resource.type=\"global\""
      comparison      = "COMPARISON_GT"
      threshold_value = 102400
      duration        = "1800s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "The pool project is storing logs fast. Runbook: ${local.pool_log_volume_runbook}"
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.owner_email.name]
}
