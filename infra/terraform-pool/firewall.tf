# Pool firewall (ADR-020). No rule has target tags, so each covers every VM.
# No two overlapping rules share a priority, so the order never rests on deny
# winning a tie.
# There are no ingress allow rules: replies come back through connection
# tracking, and the implied ingress rule denies everything else.

# Before the web allow: 443 to an internal address must stay blocked. Traffic
# to the metadata server (169.254.169.254) is always allowed, whatever this says.
resource "google_compute_firewall" "deny_internal_egress" {
  project            = var.pool_project_id
  name               = "pool-deny-internal-egress"
  network            = google_compute_network.pool.id
  direction          = "EGRESS"
  priority           = 100
  destination_ranges = local.internal_ranges

  deny {
    protocol = "all"
  }

  # With the VM name: internal IPs are reused. Kept whole, not sampled: it's
  # billed as generated anyway. Tenants drive the volume; logging.tf counts it
  # per VM and alerts.
  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "allow_web_egress" {
  project            = var.pool_project_id
  name               = "pool-allow-web-egress"
  network            = google_compute_network.pool.id
  direction          = "EGRESS"
  priority           = 200
  destination_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

# The implied egress rule at 65535 allows everything, so this one is needed.
# Not logged: tenants could drive its volume, and the signal isn't worth it.
resource "google_compute_firewall" "deny_all_egress" {
  project            = var.pool_project_id
  name               = "pool-deny-all-egress"
  network            = google_compute_network.pool.id
  direction          = "EGRESS"
  priority           = 65000
  destination_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }
}

# The subnet is IPv4 only; this holds if that ever changes. A rule can't mix
# IPv4 and IPv6 ranges.
resource "google_compute_firewall" "deny_all_egress_v6" {
  project            = var.pool_project_id
  name               = "pool-deny-all-egress-v6"
  network            = google_compute_network.pool.id
  direction          = "EGRESS"
  priority           = 65000
  destination_ranges = ["::/0"]

  deny {
    protocol = "all"
  }
}

# Redundant with the implied ingress deny today; stops a later allow (an IAP
# SSH rule, say) from opening VM-to-VM traffic.
#trivy:ignore:GCP-0074 -- a deny rule; the check reads any all-ports rule as an open range
resource "google_compute_firewall" "deny_internal_ingress" {
  project       = var.pool_project_id
  name          = "pool-deny-internal-ingress"
  network       = google_compute_network.pool.id
  direction     = "INGRESS"
  priority      = 100
  source_ranges = local.internal_ranges

  deny {
    protocol = "all"
  }
}
