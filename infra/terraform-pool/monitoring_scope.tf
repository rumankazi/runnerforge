# The pool in the control project's metrics scope (ADR-020 step 3), so control
# dashboards chart pool VMs. Linking needs monitoring.metricsScopes.link on both
# projects, which CI doesn't hold: the owner linked it by hand and this imports
# it. In this root, not the control one: its read also looks up the pool, and
# the control root must keep planning while the pool is unlinked or suspended.
#
# A plan that wants to create this means the link was lost: re-link by hand
# (gcloud beta monitoring metrics-scopes create projects/runnerforge-pool
# --project=runnerforge) and re-plan.
import {
  to = google_monitoring_monitored_project.control_scope
  id = "locations/global/metricsScopes/runnerforge/projects/${var.pool_project_id}"
}

resource "google_monitoring_monitored_project" "control_scope" {
  metrics_scope = "locations/global/metricsScopes/runnerforge"
  name          = var.pool_project_id
  # A destroy fails at once instead of trying an unlink CI can't do.
  deletion_policy = "PREVENT"

  # Create and delete retry every 403 for 20 minutes by default, holding the
  # state lock; a lost link should fail fast instead.
  timeouts {
    create = "2m"
    delete = "2m"
  }

  lifecycle {
    prevent_destroy = true
  }
}
