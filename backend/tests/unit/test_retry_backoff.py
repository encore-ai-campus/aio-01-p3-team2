"""Transient retries use deterministic, bounded exponential jitter parameters."""

import asyncio

import pytest
from backend.app.orchestration.retry import wait_before_retry


def test_retry_backoff_uses_jittered_exponential_delay_with_four_second_cap() -> None:
    delays: list[float] = []
    bounds: list[tuple[float, float]] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    def capture_uniform(low: float, high: float) -> float:
        bounds.append((low, high))
        return high

    async def run() -> None:
        await wait_before_retry(1, sleep=capture_sleep, uniform=capture_uniform)
        await wait_before_retry(4, sleep=capture_sleep, uniform=capture_uniform)
        await wait_before_retry(8, sleep=capture_sleep, uniform=capture_uniform)

    asyncio.run(run())

    assert bounds == [(0.125, 0.25), (1.0, 2.0), (2.0, 4.0)]
    assert delays == [0.25, 2.0, 4.0]


def test_retry_backoff_rejects_invalid_attempt_count() -> None:
    with pytest.raises(ValueError, match="양의 정수"):
        asyncio.run(wait_before_retry(0))
