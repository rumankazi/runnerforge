# Alert policies for the orchestrator. Three load-bearing alerts route to
# email (page-equivalent at this scale) plus one soft-warning that observes
# cold-start latency without paging on quiet periods.

# Fast burn (10x) on the availability SLO. With a 1h lookback the SLO
# would exhaust its monthly error budget in roughly 3 days at this rate —
# action is required well before that. select_slo_burn_rate is the
# Monitoring-native filter for SLO burn signals; no aggregation needed
# because the SLO already yields a single time series.
resource "google_monitoring_alert_policy" "availability_burn_rate" {
  display_name = "Availability SLO fast burn (10x over 1h)"
  combiner     = "OR"
  severity     = "CRITICAL"

  conditions {
    display_name = "Burn rate > 10 over 1h"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability.name}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "0s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
  alert_strategy {
    auto_close = "1800s"
  }
}

# Fast burn (10x) on the time-to-register SLO. Same shape as the
# availability burn-rate; separate policy so notifications carry the right
# title and runbooks can branch on which SLI degraded.
resource "google_monitoring_alert_policy" "time_to_register_burn_rate" {
  display_name = "Time-to-Register SLO fast burn (10x over 1h)"
  combiner     = "OR"
  severity     = "CRITICAL"

  conditions {
    display_name = "Burn rate > 10 over 1h"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.time_to_register.name}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "0s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
  alert_strategy {
    auto_close = "1800s"
  }
}

# Ratio of VM creation outcomes that ended in failure or timeout, over a
# 5-minute window. Total denominator is every outcome event so the ratio
# is intuitive (failures + timeouts / all). DELTA counters need an
# ALIGN_RATE aligner with a SUM reducer to collapse per-label streams.
resource "google_monitoring_alert_policy" "vm_creation_failure_ratio" {
  # Wait for the log-based metric to become queryable. The alert API
  # rejects references to metrics that exist in TF state but haven't yet
  # propagated to the metric registry (~10min worst case).
  depends_on = [time_sleep.wait_for_log_metrics]

  display_name = "VM creation failure ratio > 5% (5m)"
  combiner     = "OR"
  severity     = "ERROR"

  conditions {
    display_name = "failure+timeout / total > 5%"
    condition_threshold {
      filter             = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.vm_creation_outcome_count.name}\" resource.type=\"cloud_run_revision\" metric.label.outcome=monitoring.regex.full_match(\"failure|timeout\")"
      denominator_filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.vm_creation_outcome_count.name}\" resource.type=\"cloud_run_revision\""
      comparison         = "COMPARISON_GT"
      threshold_value    = 0.05
      duration           = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}

# Cold-start latency soft warning. The metric only emits samples on cold
# starts, so during warm-stay periods there is no data — and that's
# expected behaviour, not a degraded signal. evaluation_missing_data set
# to INACTIVE prevents the policy from opening or holding an incident
# during quiet times. Threshold 8s is a placeholder until we have
# baseline data; re-tune after a week of observations.
resource "google_monitoring_alert_policy" "cold_start_p95" {
  display_name = "Cold-start latency p95 > 8s (5m)"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "startup_latencies p95 over 8s"
    condition_threshold {
      filter                  = "metric.type=\"run.googleapis.com/container/startup_latencies\" resource.type=\"cloud_run_revision\" resource.labels.service_name=\"${google_cloud_run_v2_service.orchestrator.name}\""
      comparison              = "COMPARISON_GT"
      threshold_value         = 8000
      duration                = "300s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}

# Sweep is the orphan-VM safety net — Cloud Scheduler runs /sweep DAILY at
# 00:12 UTC (see scheduler.tf). A missed daily run means the scheduler is
# broken or the /sweep handler is degraded, and orphan VMs start
# accumulating. Duration of 26h tolerates the daily cadence + slack for
# clock skew and the one configured retry — fires only when we've genuinely
# missed the daily window.
resource "google_monitoring_alert_policy" "sweep_liveness" {
  display_name = "Sweep has not completed in 26h"
  combiner     = "OR"
  severity     = "ERROR"

  conditions {
    display_name = "Absent sweep_checked_count for 93600s"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/sweep_checked_count\" resource.type=\"cloud_run_revision\""
      duration = "93600s"

      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_COUNT"
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}

# Webhook 4xx rejections above the expected scanner baseline. /webhook is a
# public endpoint and attracts background probe traffic, so some rate of 4xx
# is normal. Sustained high volume signals HMAC rotation desync or GitHub
# payload-schema drift — both real availability incidents the SLO doesn't
# catch by design. Threshold (50/10m = 5/min) is conservative; tighten once
# we have weeks of observed baseline.
resource "google_monitoring_alert_policy" "webhook_rejection_rate" {
  display_name = "Webhook rejections > 5/min (10m)"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "Sustained webhook rejection rate"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/webhook_rejection_count\" resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 50
      duration        = "600s"

      aggregations {
        alignment_period     = "600s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}

# Lost-webhook detection: ratio of VM-creation events over queued webhook
# events. Should hover at ~1.0; sustained drop below 0.9 means we
# acknowledged a queued webhook (200 to GitHub) but failed to spin up a
# runner — workflow jobs hang in "queued" forever because GitHub doesn't
# auto-retry. Highest-priority signal for this architecture.
resource "google_monitoring_alert_policy" "lost_webhook_ratio" {
  display_name = "Lost-webhook ratio < 0.9 (5m)"
  combiner     = "OR"
  severity     = "ERROR"

  conditions {
    display_name = "VM creations / queued webhooks < 0.9"
    condition_threshold {
      filter             = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.vm_creation_count.name}\" resource.type=\"cloud_run_revision\""
      denominator_filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.webhook_event_count.name}\" resource.type=\"cloud_run_revision\" metric.label.event_type=\"queued\""
      comparison         = "COMPARISON_LT"
      threshold_value    = 0.9
      duration           = "300s"
      # Suppress the alert when no queued webhooks arrived — at our volume
      # the orchestrator can sit idle for hours, and an empty denominator
      # would otherwise trip the < 0.9 check on zero/zero.
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
  depends_on            = [time_sleep.wait_for_log_metrics]
}

# Cloud Run instance count approaching max_instances=50 (80% threshold).
# Gives time to consider raising the cap before requests start failing
# because no more instances can spin up. Saturation at the instance level is
# the upstream signal; per-instance request concurrency (capped at 10)
# saturates first but Cloud Run will still scale out — instance_count
# approaching max is the actual ceiling.
resource "google_monitoring_alert_policy" "instance_saturation" {
  display_name = "Cloud Run instance count > 40 (80% of max=50)"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "Sustained instance count near cap"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/container/instance_count\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"${google_cloud_run_v2_service.orchestrator.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 40
      duration        = "600s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}
