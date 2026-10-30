FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY evaluations ./evaluations
COPY services ./services
COPY scripts ./scripts
COPY pricing.toml ./pricing.toml
COPY fixtures/phase03 ./fixtures/phase03
RUN uv sync --locked --no-dev --no-editable \
    && useradd --uid 10001 --create-home counterforge \
    && mkdir -p /data/artifacts \
    && chown counterforge:counterforge /data/artifacts
USER counterforge
CMD ["uvicorn", "services.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
