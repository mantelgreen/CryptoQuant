"""FastAPI 通用 Depends 集合。"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header

from .db import AsyncSession, get_db
from .redis import get_redis_dep

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Any, Depends(get_redis_dep)]


async def request_id_dep(
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> str:
    return x_request_id or "no-req-id"
