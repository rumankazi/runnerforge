# Service-level objectives for the orchestrator. Both anchor to the
# Cloud-Run-typed monitoring service declared in observability_service.tf;
# burn-rate alerts in observability_alerts.tf reference these SLOs by name.

# Availability — 5xx-only as bad, non-5xx (including 4xx HMAC rejections
# and 404 scanner traffic) as good. Matches Cloud Run basic_sli semantics.
# Legitimate webhook rejection (HMAC rotation desync, payload schema drift)
# is intentionally covered by the separate webhook_rejection_count metric,
# not by this SLO — otherwise scanner traffic would burn budget unfairly.
resource "google_monitoring_slo" "availability" {
  service         = google_monitoring_service.orchestrator.service_id
  slo_id          = "availability"
  display_name    = "Availability 99.5% (calendar month)"
  goal            = 0.995
  calendar_period = "MONTH"

  basic_sli {
    availability {}
  }
}

# Time-to-Register — fraction of runner-registration events completing
# within 300s. Distribution_cut takes the histogram from the
# time_to_runner_seconds log-based metric and counts samples where
# value <= 300 as "good". Threshold 0.995 means 99.5% of registrations
# must land under 5 minutes for the SLO to hold over a 5-minute window.
resource "google_monitoring_slo" "time_to_register" {
  service         = google_monitoring_service.orchestrator.service_id
  slo_id          = "time-to-register"
  display_name    = "Time-to-Register 99.5% under 300s (calendar month)"
  goal            = 0.995
  calendar_period = "MONTH"

  windows_based_sli {
    window_period = "300s"
    good_total_ratio_threshold {
      threshold = 0.995
      performance {
        distribution_cut {
          distribution_filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.time_to_runner_seconds.name}\" resource.type=\"cloud_run_revision\""
          range {
            max = 300
          }
        }
      }
    }
  }
}
