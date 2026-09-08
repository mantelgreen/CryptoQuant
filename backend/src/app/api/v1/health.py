"""/health 健康检查。"""
from __future__ import annotations

from fastapi import APIRouter

from ...core.db import ping_db
from ...core.redis import ping_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "code": "OK",
        "message": "ok",
        "data": {
            "service": "cryptoquant-gateway",
            "db": await ping_db(),
            "redis": await ping_redis(),
        },
    }
