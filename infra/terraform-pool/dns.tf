# Query logging for tenant VMs (ADR-020). Their DNS goes through the metadata
# server, which no firewall rule sees, so this is the only record of it. Created
# after the sampling exclusion and the counter in logging.tf, so no unsampled
# query is ever stored.
resource "google_dns_policy" "pool" {
  project        = var.pool_project_id
  name           = "runnerforge-pool-query-logging"
  description    = "Query logging for tenant VMs (ADR-020)."
  enable_logging = true
  # Would put a forwarder address inside the tenant subnet.
  enable_inbound_forwarding = false

  # No alternative_name_server_config: queries would bypass Cloud DNS and its log.

  networks {
    network_url = google_compute_network.pool.id
  }

  depends_on = [
    google_project_service.enabled,
    google_logging_project_exclusion.dns_sample,
    google_logging_metric.pool_vm,
  ]
}
