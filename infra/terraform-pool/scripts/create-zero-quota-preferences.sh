#!/usr/bin/env bash
# Creates the pool's zero quota caps (ADR-020 step 2) once, by hand, so
# quotas.tf can import them: the google provider can't create a preference of
# 0 (the request would have no quotaConfig). The owner runs it with their own
# account; it skips preferences that already exist.
#
# Keep in step with the keys of local.pool_quota_caps whose value is 0 in
# ../quotas.tf: the plan fails with "Cannot import non-existent remote object"
# for any it misses.
set -euo pipefail

PROJECT="runnerforge-pool"
REGION="europe-west4"

# key|quota id|extra dimensions (after region; "-" for a global quota)
CAPS=(
	"cpus|CPUS-per-project-region|"
	"tpu_v4_podslice|TPU-PODSLICE-V4-per-project-region|"
	"tpu_ct6e|TPUS-PER-TPU-FAMILY-per-project-region|tpu_family=CT6E"
	"gpus_all_regions|GPUS-ALL-REGIONS-per-project|-"
)
for family in C2 C2D C3 N2D T2A T2D M1 M2 M3 A2; do
	key=$(echo "$family" | tr '[:upper:]' '[:lower:]')
	CAPS+=("${key}_cpus|${family}-CPUS-per-project-region|")
done
for family in C3D C4 C4A C4D E4 E4A H3 H4D N4 N4A N4D N5D Z3; do
	key=$(echo "$family" | tr '[:upper:]' '[:lower:]')
	CAPS+=("family_${key}|CPUS-PER-VM-FAMILY-per-project-region|vm_family=${family}")
done

for cap in "${CAPS[@]}"; do
	IFS='|' read -r key quota_id extra <<<"$cap"
	id="rf-pool-${key//_/-}"

	if gcloud beta quotas preferences describe "$id" --project="$PROJECT" --billing-project=runnerforge >/dev/null 2>&1; then
		echo "exists: $id"
		continue
	fi

	dims=()
	if [[ "$extra" != "-" ]]; then
		dims=(--dimensions="region=${REGION}${extra:+,$extra}")
	fi

	gcloud beta quotas preferences create \
		--project="$PROJECT" \
		--billing-project=runnerforge \
		--preference-id="$id" \
		--service=compute.googleapis.com \
		--quota-id="$quota_id" \
		"${dims[@]}" \
		--preferred-value=0 \
		--allow-high-percentage-quota-decrease \
		--justification="RunnerForge pool hard ceilings (ADR-020)." \
		--email="kaziruman@gmail.com" \
		--quiet
	echo "created: $id"
done

echo "Created or found ${#CAPS[@]} zero preferences."
