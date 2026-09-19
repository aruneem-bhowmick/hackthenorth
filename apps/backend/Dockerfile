# Production backend: API and ARQ worker share one Railway volume so P1's
# uploaded brief remains available after the API has enqueued its review job.
FROM python:3.12-slim

# Local development may supply an OS-trusted TLS-inspection certificate. The
# glob is empty in Railway, where this remains a no-op.
RUN --mount=type=bind,source=certs,target=/tmp/certs,ro \
    sh -c 'for f in /tmp/certs/*.crt; do [ -f "$f" ] && cp "$f" /usr/local/share/ca-certificates/; done; update-ca-certificates'

ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    UV_SYSTEM_CERTS=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UPLOADS_DIR=/data/uploads

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY packages/pipeline ./packages/pipeline
COPY packages/verdicts ./packages/verdicts
COPY config ./config
RUN uv sync --no-dev --package pincite-api --package pincite-worker

COPY apps/backend/start.sh /usr/local/bin/pincite-start
RUN chmod +x /usr/local/bin/pincite-start

EXPOSE 8000
CMD ["/usr/local/bin/pincite-start"]
