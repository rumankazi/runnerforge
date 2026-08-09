# ------- Stage 1: base — prod deps only, no project code -------
# Pinned by digest, same discipline as the SHA-pinned actions in .github: a
# floating tag means two builds of the same commit can differ. Renovate keeps
# the digest current (`docker.pinDigests` in renovate.json).
FROM public.ecr.aws/docker/library/python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS base

# Pull latest OS-level security patches at build time. The python:3.14-slim
# tag itself lags behind Debian's security repo by days when a CVE drops;
# this layer keeps us current without waiting for the slim image rebuild.
# Trivy gating is `ignore-unfixed: true` so we only fail on fixed CVEs,
# and this step ensures fixed CVEs don't linger in our image.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Drop pip and the ensurepip bundle. We install nothing with pip --- uv owns
# the venv --- but the slim image carries pip's vendored tree and ensurepip's
# bundled wheels, and Trivy reads the packages inside them. That is where the
# setuptools 70.3.0 (CVE-2025-47273) and msgpack 1.1.2 (GHSA-6v7p-g79w-8964)
# findings come from: not installed distributions (Syft's SBOM has neither,
# and `uv tree --no-dev` lists neither) but files bundled inside the image, so
# there is nothing to upgrade --- only to remove.
RUN python -m pip uninstall -y pip 2>/dev/null || true; \
    rm -rf /usr/local/lib/python3.14/ensurepip \
           /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

WORKDIR /app

# Copy uv binary from official image (no pip install needed). Digest-pinned:
# `:latest` here meant an unpinned third-party binary landing in the image on
# every build, which is the one thing in this repo that wasn't reproducible.
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /uvx /usr/local/bin/

# Keep uv's download cache out of the image. Every `uv sync` below would
# otherwise leave build-time packages (hatchling, pathspec, pluggy, ...) in
# the runtime filesystem, where they are dead weight and get picked up by
# image scanners even though nothing imports them.
ENV UV_NO_CACHE=1

# Copy lock + manifest from orchestrator subdir
COPY apps/orchestrator/pyproject.toml apps/orchestrator/uv.lock apps/orchestrator/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# ------- Stage 2: test — extends base with dev deps + src + tests; runs pytest -------
FROM base AS test

# Add dev deps
RUN uv sync --frozen --no-install-project

# Bring in source AND tests
COPY apps/orchestrator/src/ ./src/
COPY apps/orchestrator/tests/ ./tests/

# Install the project itself
RUN uv sync --frozen

# Build-step step execution - image fails to build if any test fails
RUN uv run pytest

# ------- Stage 3: runtime — extends base + src + CMD -------
FROM base AS runtime

# App code
COPY apps/orchestrator/src/ ./src/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "runnerforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
