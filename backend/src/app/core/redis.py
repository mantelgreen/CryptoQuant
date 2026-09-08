"""Redis 客户端 + Pub/Sub 封装。"""
from __future__ import annotations

from typing import Any, AsyncIterator

from redis.asyncio import ConnectionPool, Redis

from .config import get_settings

_settings = get_settings()

# 全局共享连接池
_pool: ConnectionPool | None = None


def get_redis() -> Redis:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            _settings.REDIS_URL,
            decode_responses=True,
            max_connections=64,
        )
    return Redis(connection_pool=_pool)


async def ping_redis() -> bool:
    try:
        client = get_redis()
        return bool(await client.ping())
    except Exception:
        return False


async def get_redis_dep() -> AsyncIterator[Any]:
    """FastAPI Depends：注入 Redis 客户端。"""
    client = get_redis()
    try:
        yield client
    finally:
        # 不关闭客户端，连接池复用；如需短连接可改为 aclose()
        pass
