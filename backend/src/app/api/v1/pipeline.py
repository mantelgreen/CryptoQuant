"""/api/v1/pipeline —— AI 流水线（研究板块：因子→模型→回测→评估 自动闭环）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.db import get_db
from ...core.errors import NotFound
from ...pipeline.service import (
    delete_pipeline,
    get_pipeline_detail,
    list_pipelines,
    rerun_pipeline,
    rerun_round_backtest,
    save_round_model,
    start_pipeline,
    stop_pipeline,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
settings = get_settings()

_INTERVALS = r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$"
_TERMINAL = ("succeeded", "finished", "failed", "stopped")


class PipelineStartReq(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    objective: str | None = Field(default=None, max_length=1200)
    llm_model_id: str | None = None
    research_symbols: list[str] = Field(..., min_length=1, max_length=20)
    research_interval: str = Field(..., pattern=_INTERVALS)
    research_start: datetime
    research_end: datetime
    backtest_symbol: str | None = Field(default=None, max_length=40)
    backtest_interval: str | None = Field(default=None, pattern=_INTERVALS)
    backtest_start: datetime
    backtest_end: datetime
    factor_count_per_round: int = Field(default=4, ge=1, le=5)
    max_pick: int = Field(default=6, ge=2, le=12)
    run_mode: str = Field(default="rounds", pattern=r"^(rounds|schedule)$")
    max_rounds: int = Field(default=5, ge=1, le=200)
    interval_min: int = Field(default=60, ge=1, le=1440)
    model: str = Field(default="ridge", pattern=r"^(ridge|gbdt)$")
    gbdt_objective: str = Field(default="huber", pattern=r"^(regression|regression_l1|huber)$")
    horizon: int = Field(default=1, ge=1, le=10)
    test_ratio: float = Field(default=0.0, ge=0.0, le=0.5)
    val_ratio: float = Field(default=0.15, ge=0.0, le=0.4)
    alpha: float = Field(default=1.0, ge=0.0, le=50.0)
    max_corr: float | None = Field(default=0.8, ge=0.0, lt=1.0)
    winsor: float | None = Field(default=None, ge=0.0, lt=0.5)
    feature_norm: str = Field(default="global", pattern=r"^(global|expanding)$")
    cost_bps: float = Field(default=15.0, ge=0.0, le=1000.0)
    fine_interval: str | None = Field(
        default=None, pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h)$"
    )
    agg_features: list[str] = Field(default_factory=list)
    learning_rate: float = Field(default=0.05, ge=0.001, le=1.0)
    n_estimators: int = Field(default=600, ge=10, le=5000)
    max_depth: int = Field(default=3, ge=-1, le=32)
    num_leaves: int = Field(default=7, ge=2, le=256)
    min_child_samples: int = Field(default=20, ge=5, le=200)
    seed_ensemble: int = Field(default=3, ge=1, le=10)
    subsample: float = Field(default=0.9, ge=0.1, le=1.0)
    colsample_bytree: float = Field(default=0.9, ge=0.1, le=1.0)
    bt_window: int = Field(default=60, ge=20, le=240)
    bt_enter_q: float = Field(default=0.8, ge=0.5, le=0.95)
    bt_exit_q: float = Field(default=0.2, ge=0.05, le=0.5)
    thresholds: dict[str, float | int] = Field(default_factory=dict)
    stop_on_excellent: bool = True


class PipelineBacktestRerunReq(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=40)
    start: datetime
    end: datetime
    window: int = Field(default=60, ge=20, le=240)
    enter_q: float = Field(default=0.8, ge=0.5, le=0.95)
    exit_q: float = Field(default=0.2, ge=0.05, le=0.5)


@router.post("/start")
async def pipeline_start(
    req: PipelineStartReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await start_pipeline(db, req.model_dump())
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/{run_id}/rerun")
async def pipeline_rerun(
    run_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    data = await rerun_pipeline(db, run_id)
    if data is None:
        raise NotFound("pipeline_not_found")
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/{run_id}/rounds/{round_no}/save-model")
async def pipeline_save_round_model(
    run_id: UUID, round_no: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    data = await save_round_model(db, run_id, round_no)
    if data is None:
        raise NotFound("pipeline_or_round_not_found")
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/{run_id}/rounds/{round_no}/backtest")
async def pipeline_rerun_round_backtest(
    run_id: UUID,
    round_no: int,
    req: PipelineBacktestRerunReq,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if req.end <= req.start:
        from ...core.errors import BadRequest

        raise BadRequest("backtest_end_must_be_after_start")
    data = await rerun_round_backtest(
        db, run_id, round_no, symbol=req.symbol, start=req.start, end=req.end,
        window=req.window, enter_q=req.enter_q, exit_q=req.exit_q,
    )
    if data is None:
        raise NotFound("pipeline_or_round_not_found")
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/{run_id}/stop")
async def pipeline_stop(
    run_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    ok = await stop_pipeline(db, run_id)
    if not ok:
        raise NotFound("pipeline_not_found_or_already_stopped")
    return {"code": "OK", "message": "ok", "data": {"stopped": True}}


@router.delete("/{run_id}")
async def pipeline_delete(
    run_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    ok = await delete_pipeline(db, run_id)
    if not ok:
        raise NotFound("pipeline_not_found")
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}


@router.get("")
async def pipeline_list(
    limit: int = 50, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = await list_pipelines(db, limit=limit)
    return {"code": "OK", "message": "ok", "data": rows}


@router.get("/{run_id}")
async def pipeline_detail(
    run_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    data = await get_pipeline_detail(db, run_id)
    if data is None:
        raise NotFound("pipeline_not_found")
    return {"code": "OK", "message": "ok", "data": data}


async def _progress_events(run_id: UUID, initial: dict[str, Any]) -> Any:
    yield f"data: {json.dumps({'type': 'status', **initial}, default=str)}\n\n"
    if initial.get("status") in _TERMINAL:
        return

    client = AsyncRedis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(f"ws:pipeline:{run_id}")
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
            if msg is None:
                yield ": ping\n\n"
                continue
            if msg.get("type") == "message":
                frame = msg["data"]
                yield f"data: {frame}\n\n"
                try:
                    ev = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                if ev.get("status") in _TERMINAL:
                    break
    finally:
        try:
            await pubsub.unsubscribe(f"ws:pipeline:{run_id}")
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.get("/{run_id}/events")
async def pipeline_progress_stream(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    from ...pipeline.repo import PipelineRunRepository

    run = await PipelineRunRepository(db).get(run_id)
    if not run:
        raise NotFound("pipeline_not_found")
    initial = {
        "run_id": str(run_id),
        "status": run["status"],
        "round_no": run.get("current_round"),
        "error": run.get("error"),
    }
    return StreamingResponse(
        _progress_events(run_id, initial),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
