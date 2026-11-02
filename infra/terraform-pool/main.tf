# The pool project that hosts tenant runner VMs (ADR-020). Its own root module
# and state, so a suspended or unlinked pool never blocks a control-plane apply.
terraform {
  backend "gcs" {
    # The bucket lives in the control project, so it survives a pool billing cut.
    bucket = "runnerforge-terraform-state"
    prefix = "pool"
  }
}

provider "google" {
  # Never left to the environment: CI auth exports GOOGLE_CLOUD_PROJECT=runnerforge.
  project = var.pool_project_id
  region  = local.region
  zone    = "${local.region}-a"
}
