"""/api/v1/strategy 策略与回测 —— 薄 API（业务在 strategy.engine.service / engine.job）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...strategy.engine.repo import BacktestRepository, StrategyRepository, StrategyVersionRepository
from ...strategy.engine.service import delete_backtest, delete_strategy, start_backtest
from ...strategy.engine.templates import get_templates

router = APIRouter(prefix="/strategy", tags=["strategy"])
settings = get_settings()


class CreateStrategyReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    tags: list[str] = []


class CreateVersionReq(BaseModel):
    template_id: str
    code: str = ""  # 预留：自定义策略代码（M3 支持）
    params: dict[str, Any] = {}


class RunBacktestReq(BaseModel):
    strategy_id: UUID
    version_id: UUID | None = None
    symbol: str
    interval: str = Field(..., pattern="^(15m|30m|1h|2h|4h|6h|12h|1d)$")
    start: datetime
    end: datetime
    params: dict[str, Any] = {}


@router.get("/templates")
async def list_templates() -> dict[str, Any]:
    """返回内置策略模板（隐藏模板如 ml_score 不在此列出，仅供程序化创建）。"""
    data = {
        tid: {k: v for k, v in meta.items() if k not in ("fn", "hidden")}
        for tid, meta in get_templates().items()
        if not meta.get("hidden")
    }
    return {"code": "OK", "message": "ok", "data": data}


@router.post("")
async def create_strategy(
    req: CreateStrategyReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = StrategyRepository(db)
    sid = await repo.create(name=req.name, description=req.description, tags=req.tags)
    return {"code": "OK", "message": "ok", "data": {"strategy_id": str(sid)}}


@router.get("")
async def list_strategies(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = StrategyRepository(db)
    items = await repo.list()
    return {"code": "OK", "message": "ok", "data": items}


# ===== 回测 =====
@router.post("/backtests")
async def run_backtest(
    req: RunBacktestReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        data = await start_backtest(
            db,
            strategy_id=req.strategy_id,
            version_id=req.version_id,
            symbol=req.symbol,
            interval=req.interval,
            start=req.start,
            end=req.end,
            params=req.params,
        )
    except ValueError as exc:
        code = str(exc)
        if code in ("strategy_not_found", "strategy_version_not_found"):
            raise NotFound(code) from exc
        raise BadRequest(code) from exc
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/backtests")
async def list_backtests(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = BacktestRepository(db)
    items = await repo.list_recent(limit)
    return {"code": "OK", "message": "ok", "data": items}


@router.get("/backtests/{backtest_id}")
async def get_backtest(backtest_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = BacktestRepository(db)
    item = await repo.get(backtest_id)
    if not item:
        raise NotFound("backtest_not_found")
    equity = await repo.get_equity("backtest", backtest_id)
    trades = await repo.get_trades(backtest_id)
    return {
        "code": "OK",
        "message": "ok",
        "data": {"backtest": item, "equity": equity, "trades": trades},
    }


async def _progress_events(backtest_id: UUID, initial: dict[str, Any]) -> Any:
    """SSE 生成器：先推当前快照，再订阅 worker 发布的进度直至终态。"""
    yield f"data: {json.dumps({'type': 'status', **initial}, default=str)}\n\n"

    if initial.get("status") in ("succeeded", "failed"):
        return

    client = AsyncRedis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(f"ws:backtest:{backtest_id}")
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
            if msg is None:
                yield ": ping\n\n"  # 心跳注释行，避免代理断开
                continue
            if msg.get("type") == "message":
                frame = msg["data"]
                yield f"data: {frame}\n\n"
                try:
                    ev = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                if ev.get("status") in ("succeeded", "failed"):
                    break
    finally:
        try:
            await pubsub.unsubscribe(f"ws:backtest:{backtest_id}")
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.get("/backtests/{backtest_id}/progress")
async def backtest_progress_stream(
    backtest_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE：回测实时进度（worker 阶段 → 终态）。"""
    repo = BacktestRepository(db)
    item = await repo.get(backtest_id)
    if not item:
        raise NotFound("backtest_not_found")
    initial = {
        "backtest_id": str(item["id"]),
        "status": item["status"],
        "stage": item.get("stage"),
        "pct": item.get("progress_pct"),
        "error": item.get("error"),
    }
    return StreamingResponse(
        _progress_events(backtest_id, initial),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# ===== 带参路由（放在静态路径之后注册，避免抢占 /backtests 等） =====

@router.get("/{strategy_id}")
async def get_strategy(strategy_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = StrategyRepository(db)
    item = await repo.get(strategy_id)
    if not item:
        raise NotFound("strategy_not_found")
    vrepo = StrategyVersionRepository(db)
    versions = await vrepo.list_for(strategy_id)
    return {"code": "OK", "message": "ok", "data": {"strategy": item, "versions": versions}}


@router.post("/{strategy_id}/versions")
async def create_version(
    strategy_id: UUID,
    req: CreateVersionReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if req.template_id not in get_templates():
        raise BadRequest("unknown_template")
    repo = StrategyRepository(db)
    if not await repo.get(strategy_id):
        raise NotFound("strategy_not_found")
    vrepo = StrategyVersionRepository(db)
    vid = await vrepo.create(
        strategy_id=strategy_id,
        code=req.code or f"# {req.template_id}\n",
        params={**req.params, "template_id": req.template_id},
    )
    return {"code": "OK", "message": "ok", "data": {"version_id": str(vid)}}

@router.delete("/backtests/{backtest_id}")
async def delete_backtest_record(
    backtest_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """删除一次回测（连同资金曲线与成交明细）。"""
    ok = await delete_backtest(db, backtest_id)
    if not ok:
        raise NotFound("backtest_not_found")
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}


@router.delete("/{strategy_id}")
async def delete_strategy_record(
    strategy_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """删除策略及其全部版本与回测记录。"""
    ok = await delete_strategy(db, strategy_id)
    if not ok:
        raise NotFound("strategy_not_found")
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}
