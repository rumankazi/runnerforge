resource "google_cloud_run_v2_service" "orchestrator" {
  name = "runnerforge-orchestrator"
  location = "europe-west4"

  # Ingress: who can reach the service at the network layer.
  # INGRESS_TRAFFIC_ALL = public internet (needed for Github webhooks)

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.orchestrator.email

    scaling {
      min_instance_count = 0 # scale to zero when idle (cost: $0).
      max_instance_count = 10 # Generous burst headroom for webhook spikes
    }

    # serialize requests per instance. Orchestrator makes external HTTP calls:
    # concurrency = 1 prevents head-of-line blocking and simplifies reasoning #TODO: come back to this later in phase 2 hardening
    max_instance_request_concurrency = 1

    containers {
      image = "europe-west4-docker.pkg.dev/runnerforge/orchestrator/orchestrator:${var.image_tag}"
      ports {
        container_port = 8000 # matching the orchestrator's uvicorn port
      }

      resources {
        limits = {
          cpu = "1"   # 1 vCPU is plenty for our request rate
          memory = "512Mi"  # FastAPI + httpx + GCP SDKs fit comfortably
        }
        cpu_idle = true   # allow CPU to be throttled when idle
        startup_cpu_boost = true  # 2x CPU during startup -> faster cold starts
      }

      # --- Plain env vars ---
      env {
        name = "GITHUB_APP_ID"
        value = var.github_app_id
      }

      env{
        name = "GCP_PROJECT_ID"
        value = "runnerforge"
      }

      env{
        name = "GCP_ZONE"
        value = "europe-west4-a"
      }

      env {
        name = "GITHUB_APP_PRIVATE_KEY_PATH"
        value = "/secrets/github-app-private-key"
      }

      env {
        name = "GITHUB_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.github_webhook_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "SWEEP_AUTH_TOKEN"
        value_source {
          secret_key_ref {
            secret = google_secret_manager_secret.sweep_auth_token.secret_id
            version = "latest"
          }
        }
      }

    # --- File mount for the PEM private key ---
      volume_mounts {
        name = "github-app-private-key"
        mount_path = "/secrets" # File available at /secrets/github-app-private-key
      }
    }

    # Volume definition: pulls the secret value, exposes it as a file
    volumes {
      name = "github-app-private-key"
      secret {
        secret = google_secret_manager_secret.github_app_private_key.secret_id
        items {
          version = "latest"
          path = "github-app-private-key" # file name inside the mount dir
        }
      }
    }
  }
  # Don't depend on this resource being created before the IAM grants for secrets
  # - Terraform infers that from the secrets references above
}

# Make the service publicly invocable. Github needs to POST /webhook from random IPs.
# The orchestrator's HMAC verification gates which payloads are accepted.
resource "google_cloud_run_v2_service_iam_member" "orchestrator_public" {
  project = "runnerforge"
  location = google_cloud_run_v2_service.orchestrator.location
  name = google_cloud_run_v2_service.orchestrator.name
  role = "roles/run.invoker"
  member = "allUsers"
}

# Expose the deployed URL so we can use it externally.
output "orchestrator_url" {
  value = google_cloud_run_v2_service.orchestrator.uri
  description = "Public HTTPS URL of the orchestrator Cloud Run service"
}
