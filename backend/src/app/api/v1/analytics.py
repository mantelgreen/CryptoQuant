"""/api/v1/analytics/llm —— LLM 用量统计。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...llm.usage import overview, timeseries

router = APIRouter(prefix="/analytics/llm", tags=["analytics-llm"])


@router.get("/overview")
async def llm_overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    data = await overview(db)
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/timeseries")
async def llm_timeseries(
    days: int = Query(default=30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await timeseries(db, days=days)
    return {"code": "OK", "message": "ok", "data": data}
