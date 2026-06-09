resource "google_service_account" "runnerforge_runner_vm" {
  account_id   = "runnerforge-runner-vm"
  display_name = "RunnerForge Runner VM"
  description  = "Attaches to the VMs created by orchestrator with narrow permissions"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_iam_member" "runnerforge_runner_vm_log_write" {
  project = data.google_project.current.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runnerforge_runner_vm.email}"
}

# ---- Act-as the runnerforge-runner-vm (so orchestrator can attach it to VMs) ----
resource "google_service_account_iam_member" "orchestrator_acts_as_runnerforge_runner_vm" {
  service_account_id = google_service_account.runnerforge_runner_vm.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.orchestrator.email}"
}

output "runnerforge_runner_vm_sa_email" {
  value       = google_service_account.runnerforge_runner_vm.email
  description = "Email of SA (runnerforge-runner-vm) attached to runner-vm created by orchestrator"
}
