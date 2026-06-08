#!/usr/bin/env bash
# Re-export Cloud Monitoring dashboards from the GCP console into TF state.
#
# Usage: ./sync-dashboards.sh
# After running, `terraform plan` should show no changes.
# Commit the updated JSON file(s) afterwards.

set -euo pipefail

PROJECT_NUMBER=8547588670
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARDS_DIR="${SCRIPT_DIR}/dashboards"

# Add additional dashboards here as: <id> <output_filename>.
# Find IDs with: gcloud monitoring dashboards list --format='value(name)'
DASHBOARDS=(
  "a00bf4fe-4696-47ff-ac41-aec8c1f47936 orchestrator_health.json"
)

for entry in "${DASHBOARDS[@]}"; do
  id="${entry% *}"
  filename="${entry#* }"
  echo "Syncing ${filename} (id=${id})..."
  gcloud monitoring dashboards describe \
    "projects/${PROJECT_NUMBER}/dashboards/${id}" \
    --format=json \
    | jq 'del(.name, .etag)' \
    > "${DASHBOARDS_DIR}/${filename}"
done

echo
echo "Done. Run 'terraform plan' to verify no drift."
