"""Publish side of the Redis pub/sub SSE transport (see apps/api/app/sse.py)."""

import json
from typing import Any

import redis.asyncio as redis

from worker.config import get_settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def job_channel(job_id: str) -> str:
    return f"job:{job_id}:events"


async def publish_event(job_id: str, event: str, payload: dict[str, Any]) -> None:
    r = get_redis()
    await r.publish(job_channel(job_id), json.dumps({"event": event, "data": payload}))
