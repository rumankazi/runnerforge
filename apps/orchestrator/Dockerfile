# Build context at repo-root
FROM python:3.14-slim
WORKDIR /app

# Copy uv binary from official image (no pip install needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy lock + manifest from orchestrator subdir
COPY apps/orchestrator/pyproject.toml apps/orchestrator/uv.lock apps/orchestrator/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# App code
COPY apps/orchestrator/src/ ./src/
RUN uv sync --frozen --no-dev

# Startup script
COPY scripts/startup-script.sh /app/scripts/startup-script.sh


ENV PATH="/app/.venv/bin:$PATH"
ENV STARTUP_SCRIPT_PATH=/app/scripts/startup-script.sh

CMD ["uvicorn", "runnerforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
