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
