FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "runnerforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
