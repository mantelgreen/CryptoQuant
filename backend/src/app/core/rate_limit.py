"""限速（固定窗口，Redis 实现）。

设计上：每个 key 一分钟一个计数窗口。
- INCR + EXPIRE 60s
- 同时与上游 header `X-MBX-USED-WEIGHT-1m` 校准
- 429/418 触发熔断 60s
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .redis import get_redis


@dataclass
class Bucket:
    cap_per_min: int
    window_seconds: int = 60

    def key(self, scope: str) -> str:
        # 把时间秒对齐到窗口起点，确保所有 key 共用同一窗口
        bucket_ts = int(time.time() // self.window_seconds) * self.window_seconds
        return f"ratelimit:{scope}:{bucket_ts}"


class RateLimiter:
    """简单 Redis 固定窗口限速器。

    用法：
        rl = RateLimiter(cap_per_min=4500)
        await rl.acquire(scope="binance", weight=2)
    """

    def __init__(self, cap_per_min: int, window_seconds: int = 60) -> None:
        self.bucket = Bucket(cap_per_min=cap_per_min, window_seconds=window_seconds)
        self._breaker_until: float = 0.0

    async def acquire(self, scope: str, weight: int = 1, *, max_wait_s: float = 60.0) -> None:
        if time.time() < self._breaker_until:
            raise RuntimeError(f"rate_limiter_breaker_open:{scope}")

        redis = get_redis()
        key = self.bucket.key(scope)

        # 简单实现：直接 INCR 并 EXPIRE（首次设置过期）
        pipe = redis.pipeline()
        pipe.incrby(key, weight)
        pipe.expire(key, self.bucket.window_seconds)
        new_value, _ = await pipe.execute()

        if int(new_value) > self.bucket.cap_per_min:
            # 等待到下一个窗口
            sleep_s = self.bucket.window_seconds - (time.time() % self.bucket.window_seconds)
            sleep_s = min(sleep_s, max_wait_s)
            await asyncio.sleep(sleep_s)

    def trip_breaker(self, duration_s: float = 60.0) -> None:
        self._breaker_until = time.time() + duration_s

    def reset_breaker(self) -> None:
        self._breaker_until = 0.0


# 单例：币安全局限速
_binance_limiter: RateLimiter | None = None


def get_binance_limiter() -> RateLimiter:
    global _binance_limiter
    if _binance_limiter is None:
        from .config import get_settings

        _binance_limiter = RateLimiter(cap_per_min=get_settings().BA_RATE_LIMIT_PER_MIN)
    return _binance_limiter
