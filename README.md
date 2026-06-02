# RunnerForge

Self-hosted runners on GCP for Github Actions.

**Installing the GitHub App?** See [Trust & threat model](apps/web/content/docs/design/trust.mdx) for what we ask for, what limits the damage if we're compromised, and how you can verify our claims.

## Development

The project has three "apps"

<!-- add links to the docs pages for relevant concepts -->

1. **orchestrator** : Python FastAPI App -> deployed on Cloud Run -> orchestrates the VM lifecycle
2. **web**: Next.js site for frontend
3. **infra**: Terraform manifests + Packer

Make relevant changes in appropriate apps.

For detailed structure look at [Project Structure](#project-structure) below.

### Orchestrator

Tech Stack:

- Python -> Coding language
- UV -> For package and dependency management
- FastAPI -> Web framework for building APIs
- Pytests -> Testing framework

To start developing.
Prerequisite:

- [Install Python](https://www.python.org/downloads/)
- [Install UV](https://docs.astral.sh/uv/getting-started/installation/)
- [Activate Virtual Environment (venv)](https://fastapi.tiangolo.com/virtual-environments/#create-a-project)

  ```bash
  # create virtual environment at .venv directory
  uv venv
  ```

  ```bash
  # activate venv
  source .venv/bin/activate
  ```

  Read more on how to activate venv on different platforms: [Virtual Environments](https://fastapi.tiangolo.com/virtual-environments/#__tabbed_2_2)

- [Install FastAPI](https://fastapi.tiangolo.com/#installation)

### Web

<!-- TODO: add development setup -->

### Infra

## <!-- TODO: add development setup -->

### Project Structure

Items marked `# planned` don't exist on disk yet — everything else is in the repo today.

<!-- TODO: update project directory structure after phase 1 -->

```

runnerforge/
├── apps/
│ ├── orchestrator/ # FastAPI app → Cloud Run
│ │ ├── .python-version
│ │ ├── pyproject.toml # uv manages this
│ │ ├── uv.lock # planned
│ │ ├── Dockerfile # planned
│ │ └── src/
│ │ ├── **init**.py
│ │ ├── main.py # FastAPI entry point
│ │ ├── webhook.py # planned — /webhook endpoint, signature validation
│ │ ├── router.py # planned — event-type routing
│ │ ├── github.py # planned — GitHub client (auth chain, tokens)
│ │ ├── compute.py # planned — GCE client (create/delete VMs)
│ │ ├── sweep.py # planned — /sweep endpoint for orphan cleanup
│ │ ├── logging.py # planned — structured logging helpers
│ │ └── config.py # planned — env vars, settings
│ │
│ └── web/ # Next.js (fumadocs) → Vercel
│ ├── package.json
│ ├── bun.lock
│ ├── next.config.mjs
│ ├── source.config.ts # fumadocs-mdx config
│ ├── proxy.ts
│ ├── tsconfig.json
│ ├── app/
│ │ ├── layout.tsx
│ │ ├── global.css
│ │ ├── (home)/ # marketing / landing route group
│ │ │ ├── layout.tsx
│ │ │ └── page.tsx
│ │ ├── docs/
│ │ │ ├── layout.tsx
│ │ │ └── [[...slug]]/page.tsx # fumadocs catch-all
│ │ ├── api/search/route.ts # fumadocs search endpoint
│ │ ├── og/docs/[...slug]/route.tsx # OG image generation
│ │ ├── llms.txt/route.ts # LLM-friendly index
│ │ ├── llms-full.txt/route.ts
│ │ ├── llms.mdx/docs/[[...slug]]/route.ts
│ │ └── dashboard/ # planned — user + admin dashboards
│ ├── content/
│ │ ├── docs/
│ │ │ ├── index.mdx
│ │ │ ├── design/
│ │ │ │ ├── architecture.mdx
│ │ │ │ ├── decisions.mdx
│ │ │ │ ├── webhook-flow.mdx
│ │ │ │ ├── observability.mdx
│ │ │ │ └── security.mdx
│ │ │ ├── records/ # incidents, troubleshooting, investigations
│ │ │ │ └── index.mdx
│ │ │ └── guides/
│ │ │ └── getting-started.mdx
│ │ └── images/ # excalidraw / diagram sources
│ ├── components/
│ │ ├── excalidraw.tsx
│ │ ├── mdx.tsx
│ │ └── record.tsx
│ └── lib/
│ ├── cn.ts
│ ├── layout.shared.tsx
│ ├── shared.ts
│ └── source.ts
│
├── infra/ # Terraform + Packer → GCP
│ ├── terraform/ # planned
│ │ ├── main.tf
│ │ ├── variables.tf
│ │ ├── cloud-run.tf
│ │ ├── iam.tf
│ │ ├── scheduler.tf
│ │ └── networking.tf
│ └── packer/ # planned
│ ├── runner-image.pkr.hcl
│ └── scripts/install-runner.sh # baked into the VM image
│
├── scripts/ # planned — VM startup scripts
│ └── startup.sh
│
├── .github/workflows/ # planned — CI for the project itself
│ ├── deploy-orchestrator.yml
│ ├── deploy-web.yml
│ └── build-runner-image.yml
│
└── README.md
```
