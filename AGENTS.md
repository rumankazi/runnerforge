# AGENTS.md — RunnerForge

Self-hosted GitHub Actions runners on GCP. A FastAPI **orchestrator** (Cloud Run) receives GitHub App
`workflow_job` webhooks, boots GCE spot VMs from a Packer image, registers them as runners, and sweeps
stale VMs. **Terraform** provisions all GCP resources. **apps/web** is the Next.js + Fumadocs docs site (Vercel).

## Commands (verified locally 2026-10-22)

Orchestrator — run in `apps/orchestrator/` (Python 3.14, uv; uv fetches 3.14 itself, system python is ignored):
```bash
uv sync --frozen
uv run --frozen ruff check
uv run --frozen ruff format --check
uv run --frozen bandit -r src/ -ll -ii
uv run --frozen pytest            # unit + integration, 100% branch coverage required
```
Web — run in `apps/web/` (Bun; no test suite, "test" = lint + types + build):
```bash
bun install --frozen-lockfile
bun run lint
bun run types:check               # fumadocs-mdx && next typegen && tsc --noEmit
bun run build
bun run dev
```
Infra — run in `infra/terraform/` (local = static checks only):
```bash
terraform fmt -check -recursive
terraform init -backend=false -input=false && terraform validate
tflint --recursive
trivy config .                    # needs trivy (aqua-pinned)
conftest verify -p ../policies/   # OPA policy unit tests; needs conftest (aqua-pinned)
```
Container smoke test — run from **repo root** (build context is the root):
```bash
docker build -f apps/orchestrator/Dockerfile --target test .     # runs pytest in-image
```
Aggregate CI mirror — repo root: `./scripts/pre-push-check.sh [all|workflows|infra|orchestrator|web]`

## Architecture map

```
apps/orchestrator/src/runnerforge/  FastAPI app
  main.py            routes (webhook, health, sweep)    handlers.py      workflow_job handling
  github_client.py   GitHub App JWT + API              compute_client.py GCE VM create/delete
  security.py        webhook HMAC + scheduler OIDC      sweep.py         stale-VM cleanup
  config.py          env-driven settings                machine_policy.py label → machine type
  validation.py / models.py / logger.py / tracing.py (OpenTelemetry)
apps/orchestrator/tests/  unit/ (mocked), integration/ (TestClient + respx), fixtures/
apps/web/            Next.js App Router; docs in content/docs/ (design, ADRs, runbooks)
infra/terraform/     single root module, GCS backend (bucket runnerforge-terraform-state); driven by tfaction
infra/packer/        runner VM image (Debian 12) — own release stream
infra/policies/      OPA rego policies + *_test.rego, applied to terraform plans in CI
.github/workflows/   qualify.yaml = PR gate (path-filtered → _qualify-{orchestrator,web,infra}.yaml + gate)
```
No database. State lives in GCP (VMs, Secret Manager, GCS).

## Environment

Orchestrator env vars (names only; tests set throwaway defaults in `tests/conftest.py`, so none are needed
to run tests): `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_APP_ID`, `GCP_PROJECT_ID`,
`GCP_ZONE` (default `europe-west4-a`), `RUNNER_VM_SA_EMAIL`, `EXPECTED_SCHEDULER_SA_EMAIL`, `EXPECTED_AUDIENCE`.
All are listed in `apps/orchestrator/.env.example`. Never read `.env` or key files.

Toolchains: `mise.toml` (root + per-app) and `aqua.yaml` pin terraform 1.15.5, tflint 0.62.1, trivy 0.71.0,
conftest 0.68.2. Install with `mise install` / `aqua i -a` where available.

## Conventions

- Conventional Commits, enforced on **PR titles** (`pr-title.yaml`); squash-merge; `main` is protected.
- Branches: `<type>/<short-description>` off `main`.
- Python: ruff with **default config** (no ruff config exists — adding one changes CI for every file).
- TS: eslint-config-next; no prettier.
- CI hardening: actions SHA-pinned with version comment, docker images digest-pinned,
  `persist-credentials: false`, minimal `permissions:`. Keep this for any workflow you add or edit.
- Advisory scanners (bun audit, pip-audit, trivy image) run on a schedule (`audit-deps.yaml`), not in the PR gate — on purpose.
- Releases: release-please, three independent components (orchestrator, web, runner-image). Don't hand-edit
  versions or CHANGELOGs.

## Testing rules

- Every orchestrator change ships with tests; coverage is enforced at **100% branch** via `--cov-fail-under=100`.
- Mock only external services: GitHub HTTP via `respx`, GCP clients via the conftest monkeypatches.
- Integration tests use `fastapi.testclient`; no containers or credentials needed.
- Infra changes: fmt + validate + tflint locally; policy changes need a matching `*_test.rego`.

## Do / Don't

- DO run uv commands with `--frozen` and from `apps/orchestrator/`.
- DO run the full per-app command block above before declaring done.
- DON'T run `terraform plan/apply`, `packer build`, `gcloud` deploys, or trigger deploy workflows — they need
  GCP WIF creds and touch prod. Produce a plan description for the owner instead.
- DON'T touch `infra/terraform/.tfaction/`, `.terraform.lock.hcl` (unless upgrading providers), or state.
- DON'T add a ruff/prettier config or new lint rules as a side effect of another change.
- DON'T expect `test-jobs.yaml` to run on hosted runners — it dogfoods `[runnerforge, small, spot]`.

## Gotchas

- `apps/orchestrator/uv.lock` is kept in sync with `pyproject.toml` by release-please (extra-files updater).
  `uv run --frozen` is still recommended locally to avoid unintentional lock revisions.
- `mise`/`aqua` may be absent: then trivy and conftest are missing and `pre-push-check.sh infra|all` fails at the
  trivy step; system terraform/tflint may be newer than the pins (checks still pass, but output can differ from CI).
- `docker build` context is the repo root; everything else runs inside the app directory.
