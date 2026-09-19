# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Local-dev opt-in: if certs/*.crt exists in the build context (e.g. a
# corporate/antivirus TLS-scanning root CA you've had to export locally —
# see certs/.gitkeep), trust it so pip/uv can reach PyPI during the build.
# No-op on hosts without this interception. Never committed (.gitignore) —
# machine-specific, not a product secret, irrelevant to CI/deploy targets.
RUN --mount=type=bind,source=certs,target=/tmp/certs,ro \
    sh -c 'for f in /tmp/certs/*.crt; do [ -f "$f" ] && cp "$f" /usr/local/share/ca-certificates/; done; update-ca-certificates'

ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    UV_SYSTEM_CERTS=1

RUN pip install --no-cache-dir uv

# Keep the venv outside /app so the docker-compose bind mount (used for hot
# reload in dev) doesn't shadow it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

WORKDIR /app
COPY apps/api .
RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
