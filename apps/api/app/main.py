import truststore

# Must run before anything builds an SSL context (httpx, elasticsearch-py,
# etc.): makes Python's ssl module use the OS-native trust store instead of
# certifi's bundled list, so outbound HTTPS works on networks with
# TLS-scanning middleware (e.g. corporate/antivirus) whose root CA has been
# added to the OS store (see apps/api/Dockerfile) — with zero per-call-site
# config. No-op/harmless on networks without such interception.
truststore.inject_into_ssl()

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import health, jobs, sources

settings = get_settings()

if settings.sentry_dsn_api:
    sentry_sdk.init(
        dsn=settings.sentry_dsn_api,
        environment=settings.sentry_environment,
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

app = FastAPI(title="Pincite API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(sources.router)
app.include_router(health.router)
