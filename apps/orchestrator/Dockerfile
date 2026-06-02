# ------- Stage 1: base — prod deps only, no project code -------
FROM python:3.14-slim AS base
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

RUN ls -la
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
