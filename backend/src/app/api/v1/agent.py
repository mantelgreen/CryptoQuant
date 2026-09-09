"""/api/v1/agent —— LLM Agent 能力（先落地：自然语言 → 因子草稿）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...agent.service import (
    generate_factor_draft,
    generate_factor_idea,
    pick_factors,
    review_backtest_task,
    review_training,
)

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


class IdeaReq(BaseModel):
    model_id: str | None = None


@router.post("/factor-idea")
async def factor_idea(
    req: IdeaReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """随机生成一条可量化的因子研究需求（灵感），供工坊无思路时使用。"""
    try:
        data = await generate_factor_idea(db, model_id=req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}


class ReviewReq(BaseModel):
    backtest_id: str = Field(..., min_length=1)


@router.post("/review-backtest")
async def review_backtest_endpoint(
    req: ReviewReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """对某次回测用 LLM 生成中文复盘。"""
    try:
        data = await review_backtest_task(db, req.backtest_id)
    except ValueError as exc:
        if str(exc) == "backtest_not_found":
            raise NotFound("backtest_not_found") from exc
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}


class FactorItem(BaseModel):
    code: str = Field(..., min_length=1, max_length=2000)
    name: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=500)
    ic: float | None = None
    rank_ic: float | None = None
    n_total: int | None = None
    interval: str | None = None
    decay: dict[str, float | None] | None = None


class PickReq(BaseModel):
    factors: list[FactorItem] = Field(..., min_length=2, max_length=40)
    max_pick: int = Field(default=8, ge=1, le=20)
    model_id: str | None = None


@router.post("/factor-pick")
async def factor_pick(
    req: PickReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """让 LLM 根据已实测的 IC/RankIC/衰减，从候选因子中挑选进多因子模型的特征池。"""
    try:
        data = await pick_factors(
            db,
            [f.model_dump() for f in req.factors],
            model_id=req.model_id,
            max_pick=req.max_pick,
        )
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}


class TrainReviewReq(BaseModel):
    symbol: str = Field(default="", max_length=40)
    interval: str = Field(default="", max_length=10)
    horizon: int = Field(default=1, ge=1, le=10)
    model: str = Field(default="", max_length=20)
    engine: str = Field(default="", max_length=40)
    n_train: int | None = None
    n_test: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[dict[str, Any]] = Field(default_factory=list)
    oos_charts: dict[str, Any] = Field(default_factory=dict)
    learning: dict[str, Any] | None = None
    model_id: str | None = None


@router.post("/train-review")
async def train_review(
    req: TrainReviewReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """让 LLM 诊断一次多因子训练结果；效果不理想时给出特征精筛建议（可一键重训）。"""
    try:
        data = await review_training(db, req.model_dump(), model_id=req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}
