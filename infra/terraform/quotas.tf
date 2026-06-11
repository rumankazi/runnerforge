# GCP quota preferences (IaC-managed since PR 5 of Phase 3.4).
#
# What this file owns: each `google_cloud_quotas_quota_preference` declares
# a preferred quota value for our project. GCP fills in `granted_value` and
# state details — those are computed attributes. Future quota bumps go
# through this file: edit `preferred_value`, PR, plan, apply.
#
# Capacity math for europe-west4 (verified 2026-06-11):
#
#   Per-family CPU quotas (GCP defaults, not requested here — visible via
#   `gcloud compute regions describe europe-west4 --format='value(quotas)'`):
#     E2_CPUS   = 24    (binding for E2 family — why we don't default to e2)
#     N2_CPUS   = 200   (50× n2-standard-4 — comfortable headroom)
#     CPUS      = 200   (N1-only in modern regions; doesn't gate E2/N2)
#     N2D_CPUS  = 16    (tiny)
#     C2_CPUS   = 8     (tiny)
#
#   User-requested (the preferences in this file):
#     INSTANCES         = 200    → 200 concurrent VMs total
#     SSD_TOTAL_GB      = 1500   → 30-VM hard cap (50 GB per runner) ← BINDING
#     PREEMPTIBLE_CPUS  = 200 (preferred) / 0 (granted, DENIED)
#                              → spot is blocked until manually re-requested;
#                                see the resource comment below
#
#   Effective ceiling: SSD binds at ~30 concurrent VMs for sizes ≤ std-8;
#   N2_CPUS binds before SSD only at xlarge (12 concurrent).
#
# Importing existing preferences: each was created via `gcloud alpha quotas
# preferences create` and adopted into TF state via `terraform import` during
# the PR 5 prep work. The UUIDs in the resource names come from the auto-
# generated preference IDs (visible via `gcloud alpha quotas preferences list`).

resource "google_cloud_quotas_quota_preference" "instances" {
  provider      = google.quotas
  parent        = "projects/${data.google_project.current.project_id}"
  name          = "747e2c9c-db9e-4265-a749-0b432e5965bf"
  service       = "compute.googleapis.com"
  quota_id      = "INSTANCES-per-project-region"
  contact_email = "kaziruman@gmail.com"
  justification = "Codified into IaC after the original gcloud out-of-band request. See infra/terraform/quotas.tf for capacity-math context."

  dimensions = {
    region = "europe-west4"
  }

  quota_config {
    preferred_value = 200
  }
}

resource "google_cloud_quotas_quota_preference" "ssd_total_gb" {
  provider      = google.quotas
  parent        = "projects/${data.google_project.current.project_id}"
  name          = "1573af41-ad24-4b4b-813b-731120d2a4c1"
  service       = "compute.googleapis.com"
  quota_id      = "SSD-TOTAL-GB-per-project-region"
  contact_email = "kaziruman@gmail.com"
  justification = "Codified into IaC after the original gcloud out-of-band request. See infra/terraform/quotas.tf for capacity-math context."

  dimensions = {
    region = "europe-west4"
  }

  quota_config {
    preferred_value = 1500
  }
}

# DENIED by GCP's auto-evaluator on 2026-06-11. `granted_value` is computed and
# will read 0 until a re-request succeeds. The TF resource still owns the
# preference object so the next attempt is a tracked code change rather than
# another out-of-band gcloud call. To re-request:
#   1. File via the GCP Console "Quotas & system limits" UI with a written
#      justification (auto-evaluator denies low-context low-history requests).
#   2. Once granted, terraform refresh will pick up the new granted_value;
#      preferred_value in this file already matches the request.
resource "google_cloud_quotas_quota_preference" "preemptible_cpus" {
  provider      = google.quotas
  parent        = "projects/${data.google_project.current.project_id}"
  name          = "570369b4-fab1-4286-8618-7ec6acb528f5"
  service       = "compute.googleapis.com"
  quota_id      = "PREEMPTIBLE-CPUS-per-project-region"
  contact_email = "kaziruman@gmail.com"
  justification = "Codified into IaC after the original gcloud request was denied. Re-request through console pending. See infra/terraform/quotas.tf for capacity-math context."

  dimensions = {
    region = "europe-west4"
  }

  quota_config {
    preferred_value = 200
  }
}
