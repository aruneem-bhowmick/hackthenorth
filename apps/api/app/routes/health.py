from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.schemas import HealthResponse
from app.smoke import (
    check_browserbase,
    check_courtlistener,
    check_elastic,
    check_gptzero,
    check_openai,
    check_postgres,
    check_redis,
    check_sentry,
)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    dependencies = {
        "postgres": await check_postgres(session),
        "redis": await check_redis(),
        "courtlistener": await check_courtlistener(settings),
        "openai": await check_openai(settings),
        "elastic": await check_elastic(settings),
        "browserbase": await check_browserbase(settings),
        "gptzero": await check_gptzero(settings),
        "sentry": check_sentry(settings),
    }
    return HealthResponse(status="ok", dependencies=dependencies)
