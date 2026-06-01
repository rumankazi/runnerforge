resource "google_secret_manager_secret" "github_webhook_secret" {
  secret_id = "github-webhook-secret"
  replication {
    auto {

    }
  }
}

resource "google_secret_manager_secret" "github_app_private_key" {
  secret_id = "github-app-private-key"
  replication {
    auto{

    }
  }
}
