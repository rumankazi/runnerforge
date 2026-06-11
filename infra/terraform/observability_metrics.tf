# Log-based metrics for the orchestrator. Every metric scopes to the
# Cloud Run revision serving traffic and to a single structured log message
# (or access-log shape). Label dimensions intentionally include fields the
# source code may not always populate yet — empty label values are valid in
# Cloud Logging and let dashboards/alerts slice on future-emitted fields
# without panel rewrites.

locals {
  # Every metric scopes to the orchestrator Cloud Run service. Concrete
  # filter clauses (jsonPayload.message=..., httpRequest.requestMethod=...)
  # are appended per-metric in the resource bodies below.
  log_metric_resource_filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.orchestrator.name}"
  EOT
}

# ---------------------------------------------------------------------------
# VM lifecycle — create
# ---------------------------------------------------------------------------

# Counts every submitted VM creation request. Sliced by machine_type and
# image_version so dashboards can attribute provisioning load to runner
# shape and image revision.
resource "google_logging_metric" "vm_creation_count" {
  name   = "vm_creation_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Submitted VM creation"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "machine_type"
      value_type = "STRING"
    }
    labels {
      key        = "image_version"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "machine_type"  = "EXTRACT(jsonPayload.machine_type)"
    "image_version" = "EXTRACT(jsonPayload.image_version)"
  }
}

# Counts terminal outcomes of VM creation operations: success, failure,
# timeout. `error_code` populates only on failures (empty otherwise) — the
# source of truth for "VM creation failure rate" alerting.
resource "google_logging_metric" "vm_creation_outcome_count" {
  name   = "vm_creation_outcome_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="VM creation outcome"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "outcome"
      value_type = "STRING"
    }
    labels {
      key        = "machine_type"
      value_type = "STRING"
    }
    labels {
      key        = "image_version"
      value_type = "STRING"
    }
    labels {
      key        = "error_code"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "outcome"       = "EXTRACT(jsonPayload.outcome)"
    "machine_type"  = "EXTRACT(jsonPayload.machine_type)"
    "image_version" = "EXTRACT(jsonPayload.image_version)"
    "error_code"    = "EXTRACT(jsonPayload.error_code)"
  }
}

# ---------------------------------------------------------------------------
# VM lifecycle — delete
# ---------------------------------------------------------------------------

# Counts every submitted VM deletion request. The `reason` label slices
# webhook-completion deletions from sweep-driven (orphan) deletions —
# sustained growth on the sweep side signals webhook-path failures.
resource "google_logging_metric" "vm_deletion_count" {
  name   = "vm_deletion_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Submitted VM deletion"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "reason"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "reason" = "EXTRACT(jsonPayload.reason)"
  }
}

# ---------------------------------------------------------------------------
# Runner registration latency — backs the Time-to-Register SLO
# ---------------------------------------------------------------------------

# Distribution of seconds between GitHub job creation and runner pickup.
# Source field `time_to_runner_seconds` is a numeric on the structured log;
# extracted as the metric value, bucketed exponentially to cover the full
# realistic range (seconds → many minutes for outliers).
resource "google_logging_metric" "time_to_runner_seconds" {
  name   = "time_to_runner_seconds"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Time for runner to be picked up by github"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "s"
    labels {
      key        = "machine_type"
      value_type = "STRING"
    }
    labels {
      key        = "image_version"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.time_to_runner_seconds)"

  label_extractors = {
    "machine_type"  = "EXTRACT(jsonPayload.machine_type)"
    "image_version" = "EXTRACT(jsonPayload.image_version)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2.0
      scale              = 1.0
    }
  }
}

# ---------------------------------------------------------------------------
# Webhook traffic — RED-method signals and rejection visibility
# ---------------------------------------------------------------------------

# Counts every POST to /webhook the access-log middleware records. The
# `response_status_code` label slices 2xx (accepted) from 4xx (rejected:
# HMAC failures, malformed payloads). Used as the denominator for
# rejection-ratio alerts and as the request-rate signal on dashboards.
resource "google_logging_metric" "webhook_request_count" {
  name   = "webhook_request_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="POST /webhook"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "response_status_code"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "response_status_code" = "EXTRACT(httpRequest.status)"
  }
}

# Counts /webhook requests we explicitly rejected (4xx). Separate from
# webhook_request_count to make "ratio of rejected" easy to alert on
# without complex filter math. A sustained non-zero rate signals HMAC
# rotation desync or GitHub payload-schema drift — both real availability
# incidents that the SLO availability signal (5xx-only) intentionally
# does not catch.
resource "google_logging_metric" "webhook_rejection_count" {
  name   = "webhook_rejection_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="POST /webhook"
    httpRequest.status>=400
    httpRequest.status<500
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "response_status_code"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "response_status_code" = "EXTRACT(httpRequest.status)"
  }
}

# ---------------------------------------------------------------------------
# Sweep — orphan VM cleanup health
# ---------------------------------------------------------------------------

# Per-scan count of active RunnerForge VMs. Distribution captures the
# fluctuating live fleet size between scans; dashboards plot this as a
# gauge-like trend.
resource "google_logging_metric" "runner_vm_count" {
  name   = "runner_vm_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="VM list scan"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.runnerforge_vm_count)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2.0
      scale              = 1.0
    }
  }
}

# Per-sweep count of VMs the sweep inspected. Indicates how much work each
# hourly sweep does — sustained growth here means orphans aren't being
# cleaned and the fleet is accumulating.
resource "google_logging_metric" "sweep_checked_count" {
  name   = "sweep_checked_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Sweep completed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.checked)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2.0
      scale              = 1.0
    }
  }
}

# Per-sweep count of VMs the sweep deleted. Non-zero values are normal
# (race between webhook-deletion and sweep); spikes indicate the webhook
# path is failing to delete completed runners.
resource "google_logging_metric" "sweep_deleted_count" {
  name   = "sweep_deleted_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Sweep completed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.deleted)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2.0
      scale              = 1.0
    }
  }
}

# Per-sweep count of errors. The sweep continues on individual failures
# and tallies them; sustained non-zero error counts mean the sweep itself
# is degraded (e.g., compute API throttling, IAM regression).
resource "google_logging_metric" "sweep_error_count" {
  name   = "sweep_error_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Sweep completed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.error_count)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2.0
      scale              = 1.0
    }
  }
}

# ---------------------------------------------------------------------------
# Lost-webhook detection — anchor for the "queued events received vs. VMs
# created" ratio. Counts every RunnerForge-labeled webhook the handler
# accepts, sliced by event_type (queued, in_progress, completed). Sustained
# divergence between queued-event count and vm_creation_count signals that
# the orchestrator is silently dropping work GitHub thinks it delivered.
# ---------------------------------------------------------------------------

resource "google_logging_metric" "webhook_event_count" {
  name   = "webhook_event_count"
  filter = <<-EOT
    ${local.log_metric_resource_filter}
    jsonPayload.message="Webhook event received"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "event_type"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "event_type" = "EXTRACT(jsonPayload.event_type)"
  }
}

# Cloud Monitoring SLOs and alert policies that reference a log-based metric
# validate the metric exists at create time, but log-based metrics take up
# to ~10 minutes to become queryable after creation. On a fresh apply, that
# lag races the dependent resources and the API returns
# "Cannot find metric(s) that match type = ...". Block dependent resources
# behind this sleep so the metric registry has time to catch up.
resource "time_sleep" "wait_for_log_metrics" {
  depends_on = [
    google_logging_metric.vm_creation_count,
    google_logging_metric.vm_creation_outcome_count,
    google_logging_metric.vm_deletion_count,
    google_logging_metric.time_to_runner_seconds,
    google_logging_metric.webhook_request_count,
    google_logging_metric.webhook_rejection_count,
    google_logging_metric.webhook_event_count,
    google_logging_metric.runner_vm_count,
    google_logging_metric.sweep_checked_count,
    google_logging_metric.sweep_deleted_count,
    google_logging_metric.sweep_error_count,
  ]

  create_duration = "120s"
}
