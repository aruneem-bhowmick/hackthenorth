"""Redis pub/sub as the SSE transport (FR-SYS-003, SPEC.md §8.3).

SPEC's architecture diagram provisions Redis as the job queue; it doesn't
name an SSE transport. Reusing the already-provisioned Redis instance for
pub/sub avoids adding a second mechanism just for event fan-out.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

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


async def subscribe_events(job_id: str) -> AsyncIterator[dict[str, Any]]:
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(job_channel(job_id))
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(job_channel(job_id))
        await pubsub.aclose()
