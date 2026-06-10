# Monitoring representation of the orchestrator Cloud Run service. Parent
# for every SLO declared in this project.
#
# Declared with `basic_service.service_type = "CLOUD_RUN"` so SLOs can use
# `basic_sli { availability {} }` shorthand instead of spelling out
# request_count filters by response_code_class. The basic_service labels
# mirror the underlying Cloud Run service identifiers so this resource
# observes the same signals Cloud Run emits.
#
# Cloud Monitoring also auto-creates a parallel service entry whenever
# Cloud Run handles traffic; that one is GCP-managed and unowned by
# Terraform. SLOs land on this resource exclusively.
resource "google_monitoring_service" "orchestrator" {
  service_id   = "orchestrator"
  display_name = "RunnerForge orchestrator"

  basic_service {
    service_type = "CLOUD_RUN"
    service_labels = {
      location     = "europe-west4"
      service_name = google_cloud_run_v2_service.orchestrator.name
    }
  }
}
