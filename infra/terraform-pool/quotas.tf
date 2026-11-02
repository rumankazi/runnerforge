# Pool hard ceilings (ADR-020 step 2). They act at once and don't depend on
# billing data. infra/policies/pool_common.rego keeps a copy of the caps
# (pool_quota_caps): change both together.
#
# Never add a PREEMPTIBLE-* quota: once a project has preemptible quota, Spot
# VMs can only use it, and a preference can't be withdrawn (the API has no
# delete). Without it, Spot VMs run on the family quota, N2_CPUS here.
#
# Capacity at 32 vCPUs (machine_policy.py): 10 small, 8 medium (the default),
# 4 large or 2 xlarge VMs. CPUS_ALL_REGIONS is 32 on this project; it is pinned
# there rather than raised, so nothing waits on Google's review.
#
# E2 and N1 share the regional CPUS quota, so CPUS = 0 blocks both; N2 has its
# own pool (https://cloud.google.com/compute/resource-usage). N2A_CPUS has no
# Cloud Quotas id and stays at Google's default, bounded by CPUS_ALL_REGIONS and
# the orchestrator's machine-type policy.
#
# The provider can't create a preference of 0: it suppresses "0" against an
# empty value and leaves empty values out of the request, which then has no
# quotaConfig. The zero caps are created once by hand
# (scripts/create-zero-quota-preferences.sh) and imported below. Changing one
# later works: an update does send "0".
locals {
  region_dims = { region = local.region }

  pool_quota_caps = merge(
    {
      instances    = { id = "INSTANCES-per-project-region", dims = local.region_dims, value = 10 }
      n2_cpus      = { id = "N2-CPUS-per-project-region", dims = local.region_dims, value = 32 }
      ssd_total_gb = { id = "SSD-TOTAL-GB-per-project-region", dims = local.region_dims, value = 500 } # 10 x the 50 GB data disk
      # 10 x the 10 GB boot disk, with headroom. A bigger disk in
      # compute_client.py lowers the effective VM cap; change both together.
      disks_total_gb = { id = "DISKS-TOTAL-GB-per-project-region", dims = local.region_dims, value = 200 }
      # The regional CPUS quota: N1 and E2.
      cpus            = { id = "CPUS-per-project-region", dims = local.region_dims, value = 0 }
      tpu_v4_podslice = { id = "TPU-PODSLICE-V4-per-project-region", dims = local.region_dims, value = 0 }
      tpu_ct6e = {
        id    = "TPUS-PER-TPU-FAMILY-per-project-region"
        dims  = merge(local.region_dims, { tpu_family = "CT6E" })
        value = 0
      }
      cpus_all_regions = { id = "CPUS-ALL-REGIONS-per-project", dims = {}, value = 32 }
      gpus_all_regions = { id = "GPUS-ALL-REGIONS-per-project", dims = {}, value = 0 }
    },
    # Families with their own regional quota id.
    { for family in ["C2", "C2D", "C3", "N2D", "T2A", "T2D", "M1", "M2", "M3", "A2"] :
      "${lower(family)}_cpus" => { id = "${family}-CPUS-per-project-region", dims = local.region_dims, value = 0 }
    },
    # Families counted per VM family: the vm_family values listed for
    # europe-west4 in the pool's quota info on 2026-11-02.
    { for family in ["C3D", "C4", "C4A", "C4D", "E4", "E4A", "H3", "H4D", "N4", "N4A", "N4D", "N5D", "Z3"] :
      "family_${lower(family)}" => {
        id    = "CPUS-PER-VM-FAMILY-per-project-region"
        dims  = merge(local.region_dims, { vm_family = family })
        value = 0
      }
    },
  )
}

resource "google_cloud_quotas_quota_preference" "pool" {
  for_each = local.pool_quota_caps
  provider = google.quotas

  # A fixed id: the API has no delete, so renaming a key without a moved {}
  # block would create a second preference for the same quota.
  name          = "rf-pool-${replace(each.key, "_", "-")}"
  parent        = "projects/${var.pool_project_id}"
  service       = "compute.googleapis.com"
  quota_id      = each.value.id
  dimensions    = each.value.dims
  contact_email = "kaziruman@gmail.com"
  justification = "RunnerForge pool hard ceilings (ADR-020)."
  # Decreases of more than 10% are refused by default; the pool runs nothing yet.
  ignore_safety_checks = "QUOTA_DECREASE_PERCENTAGE_TOO_HIGH"

  quota_config {
    preferred_value = each.value.value
  }

  depends_on = [google_project_service.enabled]
}

import {
  for_each = { for key, cap in local.pool_quota_caps : key => cap if cap.value == 0 }

  to = google_cloud_quotas_quota_preference.pool[each.key]
  id = "projects/${var.pool_project_id}/locations/global/quotaPreferences/rf-pool-${replace(each.key, "_", "-")}"
}

# Google can refuse or delay a preference without failing the apply, and the
# plan shows no diff. This warns on every plan until it's granted as declared.
check "pool_quota_caps_granted" {
  assert {
    condition = alltrue([for key, pref in google_cloud_quotas_quota_preference.pool :
      !pref.reconciling &&
      tonumber(coalesce(pref.quota_config[0].granted_value, "0")) == tonumber(coalesce(pref.quota_config[0].preferred_value, "0"))
    ])
    error_message = "A pool quota preference is pending or was not granted as declared; see gcloud beta quotas preferences list --project=runnerforge-pool."
  }
}
