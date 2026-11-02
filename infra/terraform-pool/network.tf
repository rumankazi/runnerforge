resource "google_compute_network" "pool" {
  project                 = var.pool_project_id
  name                    = "runnerforge-pool"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  # Cloud NAT sends traffic through the default internet route; keep it.
  delete_default_routes_on_create = false
  enable_ula_internal_ipv6        = false

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "pool" {
  project       = var.pool_project_id
  name          = "runnerforge-pool-${local.region}"
  network       = google_compute_network.pool.id
  region        = local.region
  ip_cidr_range = "10.10.0.0/24"
  stack_type    = "IPV4_ONLY"
  # Cloud NAT turns this on for the ranges it serves anyway; stating it keeps
  # the plan honest. VMs have no service account, so Google APIs see them as
  # anonymous callers.
  private_ip_google_access = true

  # No flow logs: tenants control their volume (see .trivyignore).
}

# Both NAT IPs are reserved, so an abuse report against one can be traced
# through the NAT log to a VM.
resource "google_compute_address" "nat" {
  for_each = toset(["1", "2"])

  project      = var.pool_project_id
  name         = "runnerforge-pool-nat-${each.key}"
  region       = local.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_router" "pool" {
  project = var.pool_project_id
  name    = "runnerforge-pool-router"
  network = google_compute_network.pool.id
  region  = local.region

  # no `bgp {}` block: this router is NAT-only

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_router_nat" "pool" {
  project = var.pool_project_id
  name    = "runnerforge-pool-nat"
  router  = google_compute_router.pool.name
  region  = google_compute_router.pool.region

  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [for address in google_compute_address.nat : address.self_link]
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.pool.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  # Static allocation: every VM gets a fixed block of 8192 ports, so IP:port
  # maps to one VM. 2 IPs x 64,512 ports fit 15 VMs; the 10-VM cap uses 63%.
  enable_dynamic_port_allocation      = false
  enable_endpoint_independent_mapping = false
  min_ports_per_vm                    = 8192

  # Every translation and drop, for abuse tracing. Tenants drive the volume;
  # sampling comes before any VM runs here (ADR-020 step 2).
  log_config {
    enable = true
    filter = "ALL"
  }
}

output "nat_ips" {
  description = "The pool's outbound IPs."
  value       = [for address in google_compute_address.nat : address.address]
}

output "pool_subnetwork_self_link" {
  description = "For the orchestrator's RUNNER_VM_SUBNETWORK (ADR-020 step 4)."
  value       = google_compute_subnetwork.pool.self_link
}
