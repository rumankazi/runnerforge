<h1 align="center">RunnerForge</h1>

<p align="center">Self-hosted runners on GCP for GitHub Actions.</p>

<p align="center">
  <a href="https://runnerforge.vercel.app"><b>Documentation</b></a> ·
  <a href="https://runnerforge.vercel.app/docs/design/architecture"><b>Architecture</b></a> ·
  <a href="https://runnerforge.vercel.app/docs/design/decisions"><b>ADRs</b></a>
</p>

<p align="center">
  <a href="https://github.com/rumankazi/runnerforge/releases"><img src="https://img.shields.io/github/v/release/rumankazi/runnerforge?label=orchestrator&color=blue" alt="Latest Release" /></a>
  <a href="https://www.conventionalcommits.org/"><img src="https://img.shields.io/badge/commits-conventional-yellow" alt="Conventional Commits" /></a>
  <img src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white" alt="Python 3.14" />
  <img src="https://img.shields.io/badge/fastapi-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/terraform-1.15.5-7B42BC?logo=terraform&logoColor=white" alt="Terraform 1.15.5" />
  <img src="https://img.shields.io/badge/next.js-000?logo=nextdotjs&logoColor=white" alt="Next.js" />
  <img src="https://img.shields.io/badge/bun-000?logo=bun&logoColor=white" alt="Bun" />
</p>

<p align="center">
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-orchestrator.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-orchestrator.yaml/badge.svg?branch=main" alt="Deploy Orchestrator" /></a>
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-infra.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-infra.yaml/badge.svg?branch=main" alt="Deploy Infra" /></a>
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-runner-image.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/deploy-runner-image.yaml/badge.svg?branch=main" alt="Deploy Runner Image" /></a>
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/drift-detection.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/drift-detection.yaml/badge.svg?branch=main" alt="Drift Detection" /></a>
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/codeql.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/codeql.yaml/badge.svg?branch=main" alt="CodeQL" /></a>
  <a href="https://github.com/rumankazi/runnerforge/actions/workflows/zizmor.yaml"><img src="https://github.com/rumankazi/runnerforge/actions/workflows/zizmor.yaml/badge.svg?branch=main" alt="Zizmor" /></a>
</p>

**Installing the GitHub App?** See [Trust & threat model](apps/web/content/docs/design/trust.mdx) for what we ask for, what limits the damage if we're compromised, and how you can verify our claims.

## Quick start

```bash
mise install           # installs Python, uv, Node, terraform versions from mise.toml
aqua i -a              # installs CI tooling (terraform, trivy, tflint, conftest)

# Orchestrator
cd apps/orchestrator && uv run pytest

# Web
cd apps/web && bun install && bun run dev

# Infra (read-only, locally)
cd infra/terraform && terraform validate
```

Before pushing, run the pre-push check script (somewhat mirrors CI):

```bash
./scripts/pre-push-check.sh               # all checks
./scripts/pre-push-check.sh orchestrator  # or scope to one app
```

## Project layout

```
runnerforge/
├── apps/orchestrator/    # FastAPI app → Cloud Run
├── apps/web/             # Next.js + Fumadocs docs site → Vercel
├── infra/terraform/      # GCP infra (TF apply automated via deploy-infra.yaml)
├── infra/packer/         # Runner VM image build
├── infra/policies/       # OPA policies (rego) enforced in CI
├── .github/workflows/    # CI/CD (qualify, deploy-*, drift-detection)
└── scripts/              # Dev tooling (pre-push checks)
```

## Contributing

1. **Open an issue first** for non-trivial changes — discussing the approach saves wasted work.
2. **Branch off `main`**: `git checkout -b <type>/<short-description>` (e.g., `feat/spot-vms`).
3. **Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/)** — the `pr-title.yaml` check enforces this on PR titles. Common prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `style:`, `perf:`.
4. **Before pushing**, run `./scripts/pre-push-check.sh` to catch lint/test/policy failures locally. Faster than waiting on CI.
5. **PR review**: required by `main-protection` ruleset. Linear history (no merge commits). Squash on merge.
6. **Required CI checks**: `Preflight`, `Gate`, plus the relevant `Qualify` job(s) for the paths you touched.

## How CI/CD works

Every PR runs `qualify.yaml`, which uses a path-aware preflight to dispatch only the relevant qualify jobs:

- **Orchestrator changes** → lint, format, pytest (100% coverage required), container build smoke test
- **Web changes** → Next.js build + lint
- **Infra changes** → `terraform fmt` + `validate` + `trivy` + `tflint` + `terraform plan` (via tfaction) + OPA policies via `conftest`

On merge to `main`, dedicated deploy workflows take over:

- `deploy-orchestrator.yaml` — Cloud Build image + Cloud Run deploy with canary ramp, gated on `prod` env approval
- `deploy-infra.yaml` — re-plans terraform, gates on `prod-terraform` env approval, applies
- `deploy-runner-image.yaml` — Packer image build + smoke test + family-alias promotion

Independently, `drift-detection.yaml` runs daily at 08:17 UTC against deployed GCP state. If the plan shows drift, tfaction opens a GitHub issue and auto-closes it once the next clean run completes. Runbooks: [terraform-drift](apps/web/content/docs/guides/runbooks/terraform-drift.mdx) · [terraform-stale-lock](apps/web/content/docs/guides/runbooks/terraform-stale-lock.mdx).

## Further reading

- **Design**: [architecture](https://runnerforge.vercel.app/docs/design/architecture) · [decisions (ADRs)](https://runnerforge.vercel.app/docs/design/decisions) · [security](https://runnerforge.vercel.app/docs/design/security) · [observability](https://runnerforge.vercel.app/docs/design/observability) · [trust model](https://runnerforge.vercel.app/docs/design/trust) · [webhook flow](https://runnerforge.vercel.app/docs/design/webhook-flow)
- **Operations**: [runbooks](https://runnerforge.vercel.app/docs/guides/runbooks) (webhook availability, terraform drift, terraform stale lock)
- **Development**: phase-by-phase implementation history in [docs/development/](https://runnerforge.vercel.app/docs/development)
