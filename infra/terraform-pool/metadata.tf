# Authoritative: any project-wide key not listed here (an `ssh-keys` entry, say)
# is removed. block-project-ssh-keys only works per instance, so the
# orchestrator sets it on each VM (ADR-020 step 4).
resource "google_compute_project_metadata" "pool" {
  project = var.pool_project_id

  metadata = {
    # Metadata ssh-keys are ignored. roles/editor and roles/owner holders can
    # still log in; the firewall (no ingress allow, no external IP) stops SSH.
    enable-oslogin             = "TRUE"
    serial-port-enable         = "FALSE"
    serial-port-logging-enable = "TRUE"
    enable-guest-attributes    = "FALSE"
  }

  depends_on = [google_project_service.enabled]
}
