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

resource "google_secret_manager_secret" "sweep_auth_token" {
  secret_id = "sweep-auth-token"

  replication {
    auto {

    }
  }
}
