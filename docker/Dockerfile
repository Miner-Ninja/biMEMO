# biMEMO application container.

# Stage 1: Build frontend
FROM node:22-slim AS frontend

WORKDIR /frontend
COPY app/frontend/package.json app/frontend/package-lock.json* ./
RUN npm ci
COPY app/frontend/ .
RUN npm run build

# Stage 2: Install Python dependencies
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS builder

WORKDIR /build
COPY pyproject.toml .
COPY app/backend/ ./app/backend/

RUN pip install --no-cache-dir ".[dev]"

# Stage 3: Runtime
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

RUN useradd --create-home --shell /bin/bash appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY pyproject.toml .
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

# Copy source from build context (parent directory)
COPY app/backend/ /app/backend/
COPY --from=frontend /frontend/dist/ /app/frontend/dist/
COPY tests/ /app/tests/
COPY scripts/ /app/scripts/
COPY docker/caddy/ /app/docker/caddy/

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data/keyfile /data/db /data/files \
    && chown -R appuser:appuser /app /data

ENV PYTHONPATH=/app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "backend.processes.supervisor"]
