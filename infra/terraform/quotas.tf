# GCP quota preferences (IaC-managed since PR 5 of Phase 3.4).
#
# What this file owns: each `google_cloud_quotas_quota_preference` declares
# a preferred quota value for our project. GCP fills in `granted_value` and
# state details — those are computed attributes. Future quota bumps go
# through this file: edit `preferred_value`, PR, plan, apply.
#
# Capacity math for europe-west4 (verified 2026-06-12):
#
#   Modern Spot VM quota model:
#     `provisioning_model = SPOT` VMs (what `compute_client.create_vm` uses
#     when policy.spot=true) consume the FAMILY-SPECIFIC CPU quota — e.g.
#     N2_CPUS for n2-standard-* sizes. The legacy `PREEMPTIBLE_CPUS` quota
#     applies only to old-style Preemptible VMs (`scheduling.preemptible=true`
#     without `provisioning_model=SPOT`) — which we don't create.
#     Verified empirically 2026-06-12: a running n2-standard-2 SPOT VM
#     increments N2_CPUS.usage by 2; PREEMPTIBLE_CPUS.usage stays at 0.
#     Earlier "we need to bump PREEMPTIBLE_CPUS to enable spot" assumption
#     was wrong; N2_CPUS=200 (default) was always sufficient.
#
#   Per-family CPU quotas (GCP defaults, NOT requested here — visible via
#   `gcloud compute regions describe europe-west4 --format='value(quotas)'`):
#     E2_CPUS   = 24    (binding for E2 family — why we don't default to e2)
#     N2_CPUS   = 200   (50× n2-standard-4 — gates both on-demand AND spot
#                        for n2 family)
#     CPUS      = 200   (N1-only in modern regions; doesn't gate E2/N2)
#     N2D_CPUS  = 16    (tiny)
#     C2_CPUS   = 8     (tiny)
#
#   User-requested (the preferences in this file):
#     INSTANCES         = 200    → 200 concurrent VMs total
#     SSD_TOTAL_GB      = 1500   → 30-VM hard cap (50 GB per runner) ← BINDING
#
#   Effective ceiling: SSD_TOTAL_GB binds at ~30 concurrent VMs for sizes
#   ≤ std-8; N2_CPUS binds before SSD only at xlarge (12 concurrent).
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

# NOTE 2026-06-12: a PREEMPTIBLE_CPUS preference (UUID 570369b4-...) exists
# in the cloud project as an artifact of the original (denied) request. It
# is intentionally NOT codified here — the modern Spot model uses
# family-specific CPU quotas (N2_CPUS), not PREEMPTIBLE_CPUS. The cloud
# artifact is harmless (granted=0, doesn't gate anything) and can't be
# deleted via the Cloud Quotas API (no DELETE method exists). Leave it
# alone; it has no operational effect.
