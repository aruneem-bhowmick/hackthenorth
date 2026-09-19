import pytest

from worker.rate_limit import CourtListenerRateLimiter


class FakeRedis:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, _script: str, _keys: int, *args: object) -> object:
        self.calls.append(args)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_reserves_tokens_with_the_shared_keys() -> None:
    redis = FakeRedis([[1, 1_000]])
    limiter = CourtListenerRateLimiter(redis, now_ms=lambda: 1_000)

    await limiter.acquire(3)

    assert redis.calls[0][0:2] == (
        "pincite:courtlistener:tokens",
        "pincite:courtlistener:token-sequence",
    )
    assert redis.calls[0][-1] == 3


@pytest.mark.asyncio
async def test_waits_and_retries_when_the_bucket_is_full() -> None:
    redis = FakeRedis([[0, 1_500], [1, 1_500]])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    limiter = CourtListenerRateLimiter(redis, now_ms=lambda: 1_000, sleep=sleep)
    await limiter.acquire(1)

    assert slept == [0.5]
    assert len(redis.calls) == 2


@pytest.mark.asyncio
async def test_rejects_an_amount_larger_than_a_single_minute_capacity() -> None:
    limiter = CourtListenerRateLimiter(FakeRedis([]), capacity=60)
    with pytest.raises(ValueError, match="between 0 and 60"):
        await limiter.acquire(61)
