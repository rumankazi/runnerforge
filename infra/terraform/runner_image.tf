# --- Runner Image Builder --- #
# Minimum perms to: build packer image, push images, attach VM logger SA to packer and smoke test VM, read logs from the smoke test VM, using identity of the workflow

resource "google_service_account" "ci_runner_image_build" {
  account_id   = "ci-runner-image-build"
  display_name = "RunnerForge Runner Image Builder"
  description  = "Impersonated by Github Actions for runner image build + publish image + view logs from VM"
}

# Allows principalSet (here the workflow) to impersonate SA ci-runner-image-build
resource "google_service_account_iam_member" "ci_runner_image_build_wif" {
  service_account_id = google_service_account.ci_runner_image_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github_pool.workload_identity_pool_id}/attribute.workflow_path/rumankazi/runnerforge/.github/workflows/deploy-runner-image.yaml"
}

# Allows SA ci-runner-image-build to create smoke test compute instance
resource "google_project_iam_member" "ci_runner_image_build_instance_admin" {
  project = data.google_project.current.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = "serviceAccount:${google_service_account.ci_runner_image_build.email}"
}

# Allows SA ci-runner-image-build to publish images to storage
resource "google_project_iam_member" "ci_runner_image_build_publish_image" {
  project = data.google_project.current.project_id
  role    = "roles/compute.storageAdmin"
  member  = "serviceAccount:${google_service_account.ci_runner_image_build.email}"
}

# Allows SA ci-runner-image-build to read from the smoke test VMs
resource "google_project_iam_member" "ci_runner_image_build_log_viewer" {
  project = data.google_project.current.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.ci_runner_image_build.email}"
}

# --- VM logger --- #
# We have ops agent enabled in our install-runner script
# This requires log writing permissions inside the created VM
# While creating a VM if nothing specified it takes the default compute service account, which could be dangerous as it will enable to spawn more instances through the VMs
# We also use this SA to attach to packer build, since by default packer build would use default compute SA, allowing the project editor role to the build vm that creates image. This SA has narrower permissions so safer to use this instead.
resource "google_service_account" "ci_vm_logger" {
  account_id   = "ci-vm-logger"
  display_name = "RunnerForge Runner Image VM Logger"
  description  = "Attaches to packer build and smoke test VM to provide log writing permissions to ops agent inside the running VM"
}

# Allow SA ci-runner-image to use SA (actAs) ci-vm-logger to attach to the packer build and smoke test VM created by ci-runner-image.
resource "google_service_account_iam_member" "ci_runner_image_build_act_as_ci_vm_logger" {
  service_account_id = google_service_account.ci_vm_logger.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci_runner_image_build.email}"
}

# Allow SA ci-vm-logger permission to write logs VMs to which it has been attached to.
resource "google_project_iam_member" "ci_vm_logger_writer" {
  project = data.google_project.current.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ci_vm_logger.email}"
}

# --- Outputs --- #

output "ci_runner_image_build_sa_email" {
  value       = google_service_account.ci_runner_image_build.email
  description = "SA email impersonated by runner-image build/deploy workflow."
}

output "ci_vm_logger_sa_email" {
  value       = google_service_account.ci_vm_logger.email
  description = "SA email attached via --service-account to the Packer build VM and smoke-test VM; provides logging.logWriter for the on-VM Cloud Ops Agent."
}
