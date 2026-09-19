"""Per-dependency smoke checks backing GET /api/health.

Satisfies the P0 exit-gate item "CourtListener, OpenAI, Elastic, Browserbase,
GPTZero credentials verified with a smoke call each" — without hard-failing
startup when a key isn't set yet (we don't have all of them). Each check
returns one of: not_configured | verified | error.
"""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas import DependencyStatus
from app.sse import get_redis

_TIMEOUT = httpx.Timeout(5.0)


async def check_postgres(session: AsyncSession) -> DependencyStatus:
    try:
        await session.execute(text("SELECT 1"))
        return DependencyStatus(status="verified")
    except Exception as exc:  # noqa: BLE001 — smoke check, report any failure
        return DependencyStatus(status="error", detail=str(exc))


async def check_redis() -> DependencyStatus:
    try:
        await get_redis().ping()
        return DependencyStatus(status="verified")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


async def check_courtlistener(settings: Settings) -> DependencyStatus:
    if not settings.courtlistener_api_token:
        return DependencyStatus(status="not_configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://www.courtlistener.com/api/rest/v4/courts/",
                params={"page_size": 1},
                headers={"Authorization": f"Token {settings.courtlistener_api_token}"},
            )
        if resp.status_code == 200:
            return DependencyStatus(status="verified")
        return DependencyStatus(status="error", detail=f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


async def check_openai(settings: Settings) -> DependencyStatus:
    if not settings.openai_api_key:
        return DependencyStatus(status="not_configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
        if resp.status_code == 200:
            return DependencyStatus(status="verified")
        return DependencyStatus(status="error", detail=f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


async def check_elastic(settings: Settings) -> DependencyStatus:
    if not settings.elastic_cloud_id or not settings.elastic_api_key:
        return DependencyStatus(status="not_configured")
    try:
        from elasticsearch import AsyncElasticsearch

        es = AsyncElasticsearch(
            cloud_id=settings.elastic_cloud_id,
            api_key=settings.elastic_api_key,
            request_timeout=5.0,
        )
        try:
            await es.info()
            return DependencyStatus(status="verified")
        finally:
            await es.close()
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


async def check_browserbase(settings: Settings) -> DependencyStatus:
    if not settings.browserbase_api_key or not settings.browserbase_project_id:
        return DependencyStatus(status="not_configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"https://api.browserbase.com/v1/projects/{settings.browserbase_project_id}",
                headers={"X-BB-API-Key": settings.browserbase_api_key},
            )
        if resp.status_code == 200:
            return DependencyStatus(status="verified")
        return DependencyStatus(status="error", detail=f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


async def check_gptzero(settings: Settings) -> DependencyStatus:
    # NOTE: TRACKS.md §2 flags GPTZero's exact endpoint/rate limits as
    # "verify at booth" — this hits the publicly documented text-prediction
    # endpoint with a trivial string, which is the closest thing to a ping
    # available without confirmed sponsor docs. Re-check against the real
    # docs once available; this consumes one quota call, not a true no-op.
    if not settings.gptzero_api_key:
        return DependencyStatus(status="not_configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.gptzero.me/v2/predict/text",
                headers={"x-api-key": settings.gptzero_api_key},
                json={"document": "Smoke test."},
            )
        if resp.status_code == 200:
            return DependencyStatus(status="verified")
        return DependencyStatus(status="error", detail=f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus(status="error", detail=str(exc))


def check_sentry(settings: Settings) -> DependencyStatus:
    # Sentry's own verification is the trace itself (checked manually against
    # the dashboard, per the P0 gate), not a REST ping — this only reports
    # whether a DSN is present.
    if not settings.sentry_dsn_api:
        return DependencyStatus(status="not_configured")
    return DependencyStatus(status="verified", detail="DSN configured; verify via a real trace")
