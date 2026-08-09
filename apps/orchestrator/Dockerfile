# ------- Stage 1: base — prod deps only, no project code -------
FROM public.ecr.aws/docker/library/python:3.14-slim AS base

# Pull latest OS-level security patches at build time. The python:3.14-slim
# tag itself lags behind Debian's security repo by days when a CVE drops;
# this layer keeps us current without waiting for the slim image rebuild.
# Trivy gating is `ignore-unfixed: true` so we only fail on fixed CVEs,
# and this step ensures fixed CVEs don't linger in our image.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# The same idea, for the pip-managed packages the slim image ships in its
# system site-packages. `apt-get upgrade` above only patches Debian packages,
# so it can't reach these. Trivy scans every Python package it finds in the
# image, not just the ones we depend on, so a fixed CVE in one of them fails
# the build even though neither is in our runtime dependency tree
# (`uv tree --no-dev` lists neither). Upgrade whichever are present, in place;
# the guard keeps this a no-op if a future base image drops one.
RUN for pkg in setuptools msgpack; do \
        if pip show "$pkg" > /dev/null 2>&1; then \
            pip install --no-cache-dir --upgrade "$pkg"; \
        fi; \
    done

WORKDIR /app

# Copy uv binary from official image (no pip install needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

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
