# Email notification channel for SLO burn-rate and critical alerts.
resource "google_monitoring_notification_channel" "email" {
  display_name = "RunnerForge Alerts"
  type         = "email"
  labels = {
    email_address = "kaziruman@gmail.com"
  }

  # force_delete defaults to false; setting it explicitly affirms intent:
  # never destroy this channel while alert policies still reference it.
  # Forcing destruction would silently sever those references and we'd lose
  # alerting on unrelated changes.
  force_delete = false
}
