"""/api/v1/sim 模拟交易（v0 手动账户）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...simtrader.service import (
    SimAccountRepository,
    account_detail,
    comparison,
    overview,
    place_market_order,
    run_auto_tick,
    symbol_activity,
)

router = APIRouter(prefix="/sim", tags=["sim"])


class CreateAccountReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class OrderReq(BaseModel):
    symbol: str = Field(..., min_length=2, max_length=20)
    side: str = Field(..., pattern="^(buy|sell)$")
    qty: float = Field(..., gt=0)


class AutoAccountReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    symbol: str = Field(..., min_length=2, max_length=20)
    interval: str = Field(..., pattern=r"^(15m|30m|1h|4h|1d)$")
    model_id: str = Field(..., min_length=1)
    enter: float = Field(default=0.02)
    exit: float = Field(default=-0.02)
    lookback: float | None = Field(default=None, ge=7)
    cadence_min: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool = True


@router.post("/accounts")
async def create_account(
    req: CreateAccountReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = SimAccountRepository(db)
    acct = await repo.create(name=req.name.strip(), account_type="manual")
    return {
        "code": "OK",
        "message": "ok",
        "data": {**acct, "current_cash": round(float(acct["current_cash"]), 2), "initial_cash": round(float(acct["initial_cash"]), 2)},
    }


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = SimAccountRepository(db)
    rows = await repo.list()
    for r in rows:
        r["initial_cash"] = round(float(r["initial_cash"]), 2)
        r["current_cash"] = round(float(r["current_cash"]), 2)
    return {"code": "OK", "message": "ok", "data": rows}


@router.get("/overview")
async def sim_overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """模拟盘板块绩效汇总（各账户最近记录净值）。"""
    data = await overview(db)
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/activity")
async def sim_activity(
    symbol: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """某币种的模拟盘成交/自动交易记录（跨账户、含账户类型）。"""
    data = await symbol_activity(db, symbol)
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/accounts/{account_id}")
async def get_account(account_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    repo = SimAccountRepository(db)
    acct = await repo.get(account_id)
    if not acct:
        raise NotFound("sim_account_not_found")
    data = await account_detail(db, acct)
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/comparison")
async def sim_comparison(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """三路（手动/策略自动/LLM）净值汇总，供对比图。"""
    data = await comparison(db)
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/auto/accounts")
async def create_auto_account(
    req: AutoAccountReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """创建策略自动账户（由 ML 模型打分驱动，纸面自动交易）。"""
    repo = SimAccountRepository(db)
    cfg = {
        "symbol": req.symbol.upper(),
        "interval": req.interval,
        "model_id": req.model_id,
        "enter": req.enter,
        "exit": req.exit,
        "enabled": req.enabled,
        "cadence_min": req.cadence_min or (30 if req.interval.endswith("d") else 5),
        "last_run_at": None,
    }
    if req.lookback:
        cfg["lookback"] = req.lookback
    acct = await repo.create(name=req.name.strip(), account_type="strategy", config=cfg)
    return {
        "code": "OK",
        "message": "ok",
        "data": {**acct, "current_cash": round(float(acct["current_cash"]), 2)},
    }


@router.post("/auto/{account_id}/tick")
async def auto_tick(account_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """立即执行一次自动决策（取最新打分 → 开/平/持）。"""
    repo = SimAccountRepository(db)
    acct = await repo.get(account_id)
    if not acct:
        raise NotFound("sim_account_not_found")
    result = await run_auto_tick(db, acct)
    if result.get("action") == "error":
        raise BadRequest(result.get("message") or "auto_tick_error")
    return {"code": "OK", "message": "ok", "data": result}


@router.post("/accounts/{account_id}/orders")
async def place_order(
    account_id: UUID,
    req: OrderReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    repo = SimAccountRepository(db)
    acct = await repo.get(account_id)
    if not acct:
        raise NotFound("sim_account_not_found")
    try:
        result = await place_market_order(
            db, acct, req.symbol.strip().upper(), req.side, req.qty
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": result}
