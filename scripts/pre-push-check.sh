#!/usr/bin/env bash
# Pre-push local checks that mirror what CI runs.
# Run manually before `git push` to catch issues without burning CI minutes.
#
# Requires the per-repo tools installed via aqua (run `aqua i -a` once).
# Orchestrator checks also require mise + uv (handled via your shell).
#
# Usage:
#   ./scripts/pre-push-check.sh                  # run all scopes
#   ./scripts/pre-push-check.sh infra            # infra only (TF + policies)
#   ./scripts/pre-push-check.sh orchestrator     # orchestrator only (ruff + pytest)
#   ./scripts/pre-push-check.sh workflows        # workflow YAML lint only

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SCOPE="${1:-all}"

heading() {
  echo
  echo "===== $1 ====="
}

check_workflows() {
  heading "Workflow YAML lint (actionlint)"
  if command -v actionlint >/dev/null 2>&1; then
    actionlint .github/workflows/*.yaml
  else
    echo "  actionlint not installed locally; skipping (CI's Actionlint check covers this)"
  fi
}

check_infra() {
  heading "Terraform fmt check"
  ( cd infra/terraform && terraform fmt -check -recursive )

  heading "Terraform validate"
  ( cd infra/terraform && terraform init -backend=false -input=false >/dev/null && terraform validate )

  heading "Trivy IaC scan"
  trivy config infra/terraform/

  heading "tflint"
  ( cd infra/terraform && tflint --recursive )

  if [ -d "infra/policies" ]; then
    heading "Conftest policy unit tests"
    conftest verify -p infra/policies/
  fi
}

check_orchestrator() {
  heading "Orchestrator: ruff check"
  ( cd apps/orchestrator && uv run ruff check )

  heading "Orchestrator: ruff format check"
  ( cd apps/orchestrator && uv run ruff format --check )

  heading "Orchestrator: pytest"
  ( cd apps/orchestrator && uv run pytest )
}

case "$SCOPE" in
  workflows)    check_workflows ;;
  infra)        check_infra ;;
  orchestrator) check_orchestrator ;;
  all)
    check_workflows
    check_infra
    check_orchestrator
    ;;
  *)
    echo "Unknown scope: $SCOPE"
    echo "Usage: $0 [workflows|infra|orchestrator|all]"
    exit 1
    ;;
esac

echo
echo "===== All checks passed ====="
