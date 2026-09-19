# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Local-dev opt-in: see apps/api/Dockerfile for why this step exists.
RUN --mount=type=bind,source=certs,target=/tmp/certs,ro \
    sh -c 'for f in /tmp/certs/*.crt; do [ -f "$f" ] && cp "$f" /usr/local/share/ca-certificates/; done; update-ca-certificates'

ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    UV_SYSTEM_CERTS=1

RUN pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

WORKDIR /app
# P1 uses the shared pipeline and verdict packages. Install this workspace from
# the root lockfile rather than creating an isolated worker environment that
# cannot resolve those internal packages.
COPY pyproject.toml uv.lock ./
COPY apps/worker ./apps/worker
COPY packages/pipeline ./packages/pipeline
COPY packages/verdicts ./packages/verdicts
COPY config ./config
WORKDIR /app/apps/worker
RUN uv sync --no-dev

CMD ["uv", "run", "arq", "worker.settings.WorkerSettings", "--watch", "."]
