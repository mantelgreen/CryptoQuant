"""/api/v1/factors 因子库与研究 —— 薄 API：只做参数解析与 HTTP 错误映射，业务全部在 factor.service。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, Conflict, NotFound
from ...factor.service import (
    check_model_usage,
    check_user_factor_usage,
    delete_saved_model,
    delete_user_factor,
    factor_run_history,
    join_user_factor,
    latest_factor_runs,
    list_factor_catalog,
    list_saved_models,
    persist_trained_model,
    run_factor_matrix,
    run_factor_research_batch,
    run_factor_research_cs_batch,
    score_saved_model,
    train_factor_model,
    verify_factor_expression,
)

router = APIRouter(prefix="/factors", tags=["factors"])

# 训练/研究/回测可选的目标粒度：排除过重的 1m/5m（1m/5m 仅保留为「细粒度增强」底库）
MAX_ML_FEATURES = 12


class ResearchReq(BaseModel):
    codes: list[str] = Field(..., min_length=1)
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


class MatrixReq(BaseModel):
    codes: list[str] = Field(..., min_length=2, max_length=MAX_ML_FEATURES)
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


class ResearchCsReq(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=MAX_ML_FEATURES)
    symbols: list[str] = Field(..., min_length=2, max_length=200)
    interval: str = Field(..., pattern=r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime
    horizon: int = Field(default=1, ge=1, le=10)
    min_names: int = Field(default=5, ge=2, le=200)
    neutralize: bool = False


class VerifyReq(BaseModel):
    expression: str = Field(..., min_length=1, max_length=500)
    symbol: str = Field(..., min_length=1)
    interval: str = Field(..., pattern=r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


class JoinFactorReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    expression: str = Field(..., min_length=1, max_length=500)
    source_model: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=lambda: ["AI"])


class MlTrainReq(BaseModel):
    codes: list[str] = Field(..., min_length=2, max_length=MAX_ML_FEATURES)
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(3m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime
    horizon: int = Field(default=1, ge=1, le=10)
    test_ratio: float = Field(default=0.25, ge=0.15, le=0.5)
    val_ratio: float = Field(default=0.0, ge=0.0, le=0.4)
    alpha: float = Field(default=1.0, ge=0.0, le=50.0)
    max_corr: float | None = Field(default=0.8, ge=0.0, lt=1.0)
    winsor: float | None = Field(default=None, ge=0.0, lt=0.5)
    feature_norm: str = Field(default="global", pattern=r"^(global|expanding)$")
    cost_bps: float = Field(default=15.0, ge=0.0, le=1000.0)
    fine_interval: str | None = Field(default=None, pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h)$")
    agg_features: list[str] = Field(default_factory=list)
    model: str = Field(default="ridge", pattern=r"^(ridge|gbdt)$")
    gbdt_objective: str = Field(default="huber", pattern=r"^(regression|regression_l1|huber)$")
    learning_rate: float = Field(default=0.05, ge=0.001, le=1.0)
    n_estimators: int = Field(default=600, ge=10, le=5000)
    max_depth: int = Field(default=3, ge=-1, le=32)
    num_leaves: int = Field(default=7, ge=2, le=256)
    min_child_samples: int = Field(default=20, ge=5, le=200)
    seed_ensemble: int = Field(default=3, ge=1, le=10)
    subsample: float = Field(default=0.9, ge=0.1, le=1.0)
    colsample_bytree: float = Field(default=0.9, ge=0.1, le=1.0)


class MlSaveReq(MlTrainReq):
    name: str = Field(..., min_length=1, max_length=120)


class ScoreReq(BaseModel):
    symbol: str = Field(..., min_length=1)
    start: datetime
    end: datetime


def _check_window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise BadRequest("end_must_be_greater_than_start")


@router.get("")
async def list_factors(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """内置因子 + 用户/AI 生成因子。"""
    data = await list_factor_catalog(db)
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/join")
async def join_factor(
    req: JoinFactorReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """把草稿（AI/手动）正式加入因子库。"""
    fid = await join_user_factor(
        db,
        name=req.name,
        description=req.description,
        expression=req.expression,
        source_model=req.source_model,
        tags=req.tags,
    )
    return {"code": "OK", "message": "ok", "data": {"factor_id": fid}}


@router.get("/join/{factor_id}/usage")
async def user_factor_usage(factor_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """查询用户因子被引用情况。"""
    usage = await check_user_factor_usage(db, factor_id)
    return {"code": "OK", "message": "ok", "data": {"bound": bool(usage["models"] or usage["strategies"]), **usage}}


@router.delete("/join/{factor_id}")
async def remove_user_factor(factor_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """删除用户/AI 因子（被模型/策略引用时拒绝）。"""
    usage = await check_user_factor_usage(db, factor_id)
    if usage["models"] or usage["strategies"]:
        raise Conflict(
            "该因子正被已保存的模型或策略版本引用，不能删除；请先移除引用它的模型特征 / 策略后再试。",
            detail=usage,
        )
    deleted = await delete_user_factor(db, factor_id)
    return {"code": "OK", "message": "ok", "data": {"deleted": deleted}}


@router.get("/runs/latest")
async def latest_runs(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """各因子最近的 IC 研究记录。"""
    rows = await latest_factor_runs(db, limit=200)
    return {"code": "OK", "message": "ok", "data": rows}


@router.get("/{code}/runs")
async def factor_runs(
    code: str,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """某因子历史研究记录（IC 演进/回看）。"""
    rows = await factor_run_history(db, code, limit=limit)
    return {"code": "OK", "message": "ok", "data": rows}


@router.post("/matrix")
async def factor_matrix(
    req: MatrixReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """多因子相关矩阵。"""
    _check_window(req.start, req.end)
    data = await run_factor_matrix(
        db,
        codes=req.codes,
        symbols=[s.upper() for s in req.symbols],
        interval=req.interval,
        start=req.start,
        end=req.end,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/verify")
async def verify_expression(
    req: VerifyReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """AI/手写因子表达式：AST 白名单校验后实测 IC（不落库）。"""
    _check_window(req.start, req.end)
    data = await verify_factor_expression(
        db,
        expression=req.expression,
        symbol=req.symbol.upper(),
        interval=req.interval,
        start=req.start,
        end=req.end,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/research")
async def research(
    req: ResearchReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """批量对因子做 IC/RankIC 研究（结果落库，可回看历史）。"""
    _check_window(req.start, req.end)
    results = await run_factor_research_batch(
        db,
        codes=req.codes,
        symbols=[s.upper() for s in req.symbols],
        interval=req.interval,
        start=req.start,
        end=req.end,
    )
    return {"code": "OK", "message": "ok", "data": results}


@router.post("/research/cs")
async def research_cross_sectional(
    req: ResearchCsReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """批量截面因子研究：同一时点跨标的 IC/RankIC/IR/t 值与多空价差（需多标的广度）。"""
    _check_window(req.start, req.end)
    data = await run_factor_research_cs_batch(
        db,
        codes=req.codes,
        symbols=[s.upper() for s in req.symbols],
        interval=req.interval,
        start=req.start,
        end=req.end,
        horizon=req.horizon,
        min_names=req.min_names,
        neutralize=req.neutralize,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/ml/train")
async def ml_train(
    req: MlTrainReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """ML 多因子训练 → 样本外评价。"""
    _check_window(req.start, req.end)
    data = await train_factor_model(
        db,
        codes=req.codes,
        symbols=[s.upper() for s in req.symbols],
        interval=req.interval,
        start=req.start,
        end=req.end,
        horizon=req.horizon,
        test_ratio=req.test_ratio,
        val_ratio=req.val_ratio,
        alpha=req.alpha,
        max_corr=req.max_corr,
        winsor=req.winsor,
        feature_norm=req.feature_norm,
        cost_bps=req.cost_bps,
        fine_interval=req.fine_interval,
        agg_features=req.agg_features,
        model=req.model,
        gbdt_objective=req.gbdt_objective,
        learning_rate=req.learning_rate,
        n_estimators=req.n_estimators,
        max_depth=req.max_depth,
        num_leaves=req.num_leaves,
        min_child_samples=req.min_child_samples,
        seed_ensemble=req.seed_ensemble,
        subsample=req.subsample,
        colsample_bytree=req.colsample_bytree,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/ml/models/save")
async def ml_save_model(
    req: MlSaveReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """训练并把模型 artifact 落库（供后续打分/复用）。"""
    _check_window(req.start, req.end)
    try:
        data = await persist_trained_model(
            db,
            codes=req.codes,
            symbols=[s.upper() for s in req.symbols],
            interval=req.interval,
            start=req.start,
            end=req.end,
            name=req.name,
            horizon=req.horizon,
            test_ratio=req.test_ratio,
            val_ratio=req.val_ratio,
            alpha=req.alpha,
            max_corr=req.max_corr,
            winsor=req.winsor,
            feature_norm=req.feature_norm,
            cost_bps=req.cost_bps,
            fine_interval=req.fine_interval,
            agg_features=req.agg_features,
            model=req.model,
            gbdt_objective=req.gbdt_objective,
            learning_rate=req.learning_rate,
            n_estimators=req.n_estimators,
            max_depth=req.max_depth,
            num_leaves=req.num_leaves,
            min_child_samples=req.min_child_samples,
            seed_ensemble=req.seed_ensemble,
            subsample=req.subsample,
            colsample_bytree=req.colsample_bytree,
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/ml/models")
async def ml_list_models(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await list_saved_models(db, limit=50)
    return {"code": "OK", "message": "ok", "data": rows}


@router.post("/ml/models/{model_id}/score")
async def ml_score_model(
    model_id: UUID,
    req: ScoreReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """用已保存模型对标的区间逐 bar 打分（训练窗口自动样本排除）。"""
    _check_window(req.start, req.end)
    try:
        data = await score_saved_model(db, model_id, req.symbol, req.start, req.end)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    if data is None:
        raise NotFound("model_not_found")
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/ml/models/{model_id}/usage")
async def ml_model_usage(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """查询模型被引用情况（策略 / 模拟盘账户）。"""
    usage = await check_model_usage(db, model_id)
    if usage is None:
        raise NotFound("model_not_found")
    bound = bool(usage["strategies"] or usage["sim_accounts"])
    return {"code": "OK", "message": "ok", "data": {"bound": bound, **usage}}


@router.delete("/ml/models/{model_id}")
async def ml_delete_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """删除模型（行 + 磁盘 artifact）；被引用时拒绝。"""
    usage = await check_model_usage(db, model_id)
    if usage is None:
        raise NotFound("model_not_found")
    if usage["strategies"] or usage["sim_accounts"]:
        raise Conflict(
            "该模型正被策略或模拟盘引用，不能删除；请先删除引用它的策略/解绑模拟盘后再试。",
            detail=usage,
        )
    data = await delete_saved_model(db, model_id)
    return {"code": "OK", "message": "ok", "data": data or {"deleted": False}}
