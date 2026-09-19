"""Shared Redis token bucket for CourtListener's valid-citation quota.

CourtListener permits 60 valid lookups per minute.  The Redis script makes the
check and reservation atomic across all arq workers so a concurrent brief does
not turn an upstream throttle into a retry cascade (NFR-PERF-003).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


COURTLISTENER_VALID_CITATIONS_PER_MINUTE = 60


class RedisScriptClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...


_RESERVE_TOKENS = """
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local amount = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window_ms)
local current = redis.call('ZCARD', KEYS[1])
if current + amount > capacity then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2] or now) + window_ms}
end
local sequence = redis.call('INCRBY', KEYS[2], amount)
for i = 1, amount do
  redis.call('ZADD', KEYS[1], now, tostring(now) .. ':' .. tostring(sequence - amount + i))
end
redis.call('PEXPIRE', KEYS[1], window_ms)
redis.call('PEXPIRE', KEYS[2], window_ms)
return {1, now}
"""


class CourtListenerRateLimiter:
    def __init__(
        self,
        redis: RedisScriptClient,
        *,
        capacity: int = COURTLISTENER_VALID_CITATIONS_PER_MINUTE,
        window_seconds: float = 60.0,
        now_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._redis = redis
        self._capacity = capacity
        self._window_ms = int(window_seconds * 1000)
        self._now_ms = now_ms or _monotonic_ms
        self._sleep = sleep

    async def acquire(self, amount: int) -> None:
        if amount < 0 or amount > self._capacity:
            raise ValueError(f"amount must be between 0 and {self._capacity}")
        if amount == 0:
            return
        while True:
            now = self._now_ms()
            result = await self._redis.eval(
                _RESERVE_TOKENS,
                2,
                "pincite:courtlistener:tokens",
                "pincite:courtlistener:token-sequence",
                now,
                self._window_ms,
                self._capacity,
                amount,
            )
            allowed, available_at = _parse_result(result)
            if allowed:
                return
            await self._sleep(max(0.01, (available_at - now) / 1000))


def _parse_result(result: object) -> tuple[bool, int]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError("unexpected CourtListener rate-limiter response")
    try:
        return bool(int(result[0])), int(result[1])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid CourtListener rate-limiter response") from exc


def _monotonic_ms() -> int:
    # Redis stores and compares epoch-like scores only within this process's
    # calls. asyncio's monotonic clock avoids wall-clock jumps but would differ
    # across workers, so use a normal timestamp at the call site instead.
    import time

    return int(time.time() * 1000)
