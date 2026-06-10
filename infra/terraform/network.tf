data "google_compute_subnetwork" "default" {
  name    = "default"
  region  = "europe-west4"
  project = data.google_project.current.project_id
}

resource "google_compute_router" "nat" {
  name    = "runnerforge-nat-router"
  region  = "europe-west4"
  network = "default"
  project = data.google_project.current.project_id

  # no `bgp {}` block — this router is NAT-only, no on-prem peering

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_router_nat" "main" {
  name    = "runnerforge-nat"
  router  = google_compute_router.nat.name
  region  = google_compute_router.nat.region
  project = data.google_project.current.project_id

  # Auto-assign NAT IP (no allowlisting needed — see ADR-009)
  nat_ip_allocate_option = "AUTO_ONLY"

  # Surgical: only the subnets we list (your D2 choice)
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  # in future if we decide to create subnetwork, we can change it here
  subnetwork {
    name                    = data.google_compute_subnetwork.default.self_link
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  # Errors-only logging (port exhaustion, translation failures)
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }

  # No prevent_destroy on the NAT itself — if we ever need to recreate it,
  # the router stays intact and recreate is fast (~30s)
}
