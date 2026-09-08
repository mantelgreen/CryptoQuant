"""/api/v1/data K 线查询 / 下载任务 / 定时任务。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...data.repository.kline_repo import (
    DownloadJobRepository,
    KlineRepository,
    ScheduledJobRepository,
)

router = APIRouter(prefix="/data", tags=["data"])
settings = get_settings()


class CreateScheduleReq(BaseModel):
    name: str
    cron: str
    symbols: list[str]
    interval: str
    enabled: bool = True


# ===== K 线查询（DB 缓存） =====
@router.get("/klines")
async def list_klines(
    symbol: str = Query(..., min_length=1),
    interval: str = Query(..., min_length=1),
    start: datetime = Query(...),
    end: datetime = Query(...),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = KlineRepository(db)
    rows = await repo.query_latest(
        symbol=symbol.upper(), interval=interval, start=start, end=end, limit=limit
    )
    return {"code": "OK", "message": "ok", "data": rows}


@router.get("/coverage")
async def kline_coverage(
    symbol: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """某交易对各颗粒度的本地数据覆盖（interval / count / min / max 时间）。"""
    repo = KlineRepository(db)
    rows = await repo.coverage(symbol=symbol.upper())
    return {"code": "OK", "message": "ok", "data": rows}


# ===== 下载任务 =====
@router.post("/downloads")
async def create_download(
    symbol: str,
    interval: str = Query(..., min_length=1),
    start: datetime = Query(...),
    end: datetime = Query(...),
    mode: str = Query(default="full", pattern="^(full|incremental)$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if end <= start:
        raise BadRequest("end_must_be_greater_than_start")
    repo = DownloadJobRepository(db)
    job_id = await repo.create(
        symbol=symbol,
        interval=interval,
        start_time=start,
        end_time=end,
        mode=mode,
    )
    # 派发到 ARQ
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await redis.enqueue_job(
            "download_klines_task",
            str(job_id),
            symbol,
            interval,
            start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(),
            _queue_name=settings.JOB_QUEUE_DATA,
        )
    finally:
        await redis.aclose()
    return {"code": "OK", "message": "ok", "data": {"job_id": str(job_id), "status": "queued"}}


@router.get("/downloads")
async def list_downloads(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = DownloadJobRepository(db)
    items = await repo.list_recent(limit=limit)
    return {"code": "OK", "message": "ok", "data": items}


@router.get("/downloads/{job_id}")
async def get_download(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = DownloadJobRepository(db)
    item = await repo.get(job_id)
    if not item:
        raise NotFound("download_job_not_found")
    return {"code": "OK", "message": "ok", "data": item}


# ===== 定时任务 =====
@router.post("/schedules")
async def create_schedule(
    req: CreateScheduleReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = ScheduledJobRepository(db)
    job_id = await repo.create(
        name=req.name, cron=req.cron, symbols=req.symbols,
        interval=req.interval, enabled=req.enabled,
    )
    return {"code": "OK", "message": "ok", "data": {"schedule_id": str(job_id)}}


@router.get("/schedules")
async def list_schedules(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = ScheduledJobRepository(db)
    items = await repo.list_enabled()
    return {"code": "OK", "message": "ok", "data": items}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = ScheduledJobRepository(db)
    await repo.delete(schedule_id)
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}
