"""/api/v1/agent —— LLM Agent 能力（先落地：自然语言 → 因子草稿）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...agent.service import generate_factor_draft, review_backtest
from ...strategy.engine.repo import BacktestRepository

router = APIRouter(prefix="/agent", tags=["agent"])


class DraftReq(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=1200)
    model_id: str | None = None


@router.post("/factor-draft")
async def factor_draft(
    req: DraftReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """自然语言 → 可校验的因子草稿（真实 LLM，需先在「模型设置」配好默认供应商）。"""
    try:
        draft = await generate_factor_draft(db, req.prompt, model_id=req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": draft}


class ReviewReq(BaseModel):
    backtest_id: str = Field(..., min_length=1)


@router.post("/review-backtest")
async def review_backtest_endpoint(
    req: ReviewReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """对某次回测用 LLM 生成中文复盘。"""
    backtest = await BacktestRepository(db).get(UUID(req.backtest_id))
    if not backtest:
        raise NotFound("backtest_not_found")
    try:
        data = await review_backtest(db, backtest)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}
