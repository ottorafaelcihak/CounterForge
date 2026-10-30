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
# PostgreSQL 16 client tools for the no-money backup/restore CLI (pg_dump/pg_restore).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/keyrings/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --locked --no-dev --no-editable \
    && useradd --uid 10001 --create-home counterforge \
    && mkdir -p /data/artifacts \
    && chown counterforge:counterforge /data/artifacts
USER counterforge
CMD ["python", "-m", "services.worker.app.main"]
