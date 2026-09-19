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
COPY apps/worker .
RUN uv sync --no-dev

CMD ["uv", "run", "arq", "worker.settings.WorkerSettings", "--watch", "."]
