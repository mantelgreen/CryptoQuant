"""/api/v1/factors 因子库与研究。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...data.repository.kline_repo import KlineRepository
from ...factor.registry import list_factor_meta
from ...factor.repo import FactorRunRepository, MlModelRepository, UserFactorRepository
from ...factor.service import matrix_factor, research_expression, research_factor
from ...factor.ml import run_ml_research, score_with_model

router = APIRouter(prefix="/factors", tags=["factors"])

_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w"}


class ResearchReq(BaseModel):
    codes: list[str] = Field(default_factory=lambda: [m["code"] for m in list_factor_meta()])
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


class MatrixReq(BaseModel):
    codes: list[str] = Field(..., min_length=2, max_length=12)
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


class VerifyReq(BaseModel):
    expression: str = Field(..., min_length=1, max_length=500)
    symbol: str = Field(..., min_length=1)
    interval: str = Field(..., pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime


async def _factor_extras(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """用户/AI 表达式因子：code(id) -> {name, description, tags, min_bars, expression}。"""
    rows = await UserFactorRepository(db).list()
    return {
        str(r["id"]): {
            "name": r["name"],
            "description": r.get("description") or "",
            "tags": r.get("tags") if isinstance(r.get("tags"), list) else ["AI"],
            "min_bars": 0,
            "expression": r.get("expression"),
            "is_user": True,
        }
        for r in rows
    }


class JoinFactorReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    expression: str = Field(..., min_length=1, max_length=500)
    source_model: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=lambda: ["AI"])


class MlTrainReq(BaseModel):
    codes: list[str] = Field(..., min_length=2, max_length=12)
    symbols: list[str] = Field(..., min_length=1, max_length=20)
    interval: str = Field(..., pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|3d|1w)$")
    start: datetime
    end: datetime
    horizon: int = Field(default=1, ge=1, le=10)
    test_ratio: float = Field(default=0.25, ge=0.15, le=0.5)
    alpha: float = Field(default=1.0, ge=0.0)
    max_corr: float | None = Field(default=0.85, ge=0.0, lt=1.0)
    model: str = Field(default="ridge", pattern=r"^(ridge|gbdt)$")


class MlSaveReq(MlTrainReq):
    name: str = Field(..., min_length=1, max_length=120)


class ScoreReq(BaseModel):
    symbol: str = Field(..., min_length=1)
    start: datetime
    end: datetime


@router.get("")
async def list_factors(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """内置因子 + 用户/AI 生成因子（表达式因子，带 is_user/expression）。"""
    builtins = list_factor_meta()
    user_repo = UserFactorRepository(db)
    rows = await user_repo.list()
    user_items = []
    for r in rows:
        tags = r.get("tags") or ["AI"]
        user_items.append(
            {
                "code": str(r["id"]),
                "name": r["name"],
                "description": r.get("description") or "",
                "tags": tags if isinstance(tags, list) else ["AI"],
                "expression": r.get("expression"),
                "source_model": r.get("source_model"),
                "is_user": True,
                "created_at": r.get("created_at"),
            }
        )
    return {"code": "OK", "message": "ok", "data": [*builtins, *user_items]}


@router.post("/join")
async def join_factor(
    req: JoinFactorReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """把草稿（AI/手动）正式加入因子库（draft 状态，可在库中查看与实测）。"""
    repo = UserFactorRepository(db)
    fid = await repo.create(
        name=req.name.strip(),
        description=req.description.strip(),
        expression=req.expression.strip(),
        source_model=req.source_model,
        tags=req.tags,
    )
    return {"code": "OK", "message": "ok", "data": {"factor_id": str(fid)}}


@router.delete("/join/{factor_id}")
async def remove_user_factor(factor_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await UserFactorRepository(db).delete(factor_id)
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}


@router.get("/runs/latest")
async def latest_runs(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """各因子最近的 IC 研究记录（按创建倒序，前端去重回看）。"""
    repo = FactorRunRepository(db)
    rows = await repo.latest(limit=200)
    return {"code": "OK", "message": "ok", "data": rows}


@router.get("/{code}/runs")
async def factor_runs(
    code: str,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """某因子历史研究记录（IC 演进/回看）。"""
    repo = FactorRunRepository(db)
    rows = await repo.by_code(code, limit=limit)
    return {"code": "OK", "message": "ok", "data": rows}


@router.post("/matrix")
async def factor_matrix(
    req: MatrixReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """多因子相关矩阵（各标的因子序列对齐后平均）。"""
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    repo = KlineRepository(db)
    extras = await _factor_extras(db)
    data = await matrix_factor(repo, req.codes, [s.upper() for s in req.symbols], req.interval, req.start, req.end, extras=extras)
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/verify")
async def verify_expression(
    req: VerifyReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """AI/手写因子表达式：AST 白名单校验后实测 IC（不落库）。"""
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    repo = KlineRepository(db)
    data = await research_expression(repo, req.expression, req.symbol.upper(), req.interval, req.start, req.end)
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/ml/train")
async def ml_train(
    req: MlTrainReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """ML 多因子训练：因子特征 → 时序切分 → Ridge 回归 → 样本外评价。"""
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    repo = KlineRepository(db)
    extras = await _factor_extras(db)
    data = await run_ml_research(
        repo,
        req.codes,
        [s.upper() for s in req.symbols],
        req.interval,
        req.start,
        req.end,
        horizon=req.horizon,
        test_ratio=req.test_ratio,
        alpha=req.alpha,
        max_corr=req.max_corr,
        model=req.model,
        extras=extras,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/ml/models/save")
async def ml_save_model(
    req: MlSaveReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """训练并把模型 artifact 落库（供后续打分/复用）。"""
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    if req.model != "ridge":
        raise BadRequest("模型落库/打分当前仅支持 ridge（线性可复用）；gbdt 可研究但不可保存打分")
    repo = KlineRepository(db)
    extras = await _factor_extras(db)
    resp = await run_ml_research(
        repo,
        req.codes,
        [s.upper() for s in req.symbols],
        req.interval,
        req.start,
        req.end,
        horizon=req.horizon,
        test_ratio=req.test_ratio,
        alpha=req.alpha,
        max_corr=req.max_corr,
        model=req.model,
        extras=extras,
        return_artifact=True,
    )
    if not resp.get("found") or "artifact" not in resp:
        raise BadRequest(resp.get("message") or "训练失败，无法保存")
    artifact = resp["artifact"]
    mrepo = MlModelRepository(db)
    mid = await mrepo.create(
        name=req.name,
        model_type=req.model,
        interval=req.interval,
        horizon=req.horizon,
        symbols=[s.upper() for s in req.symbols],
        features=artifact["features"],
        metrics={
            "oos_ic": resp.get("oos_ic"),
            "oos_rank_ic": resp.get("oos_rank_ic"),
            "hit_rate": resp.get("hit_rate"),
            "spread": resp.get("spread"),
        },
        n_train=resp.get("n_train", 0),
        n_test=resp.get("n_test", 0),
    )
    return {
        "code": "OK",
        "message": "ok",
        "data": {
            "model_id": str(mid),
            "name": req.name,
            "interval": req.interval,
            "horizon": req.horizon,
            "features": len(artifact["features"]),
            "metrics": {
                "oos_ic": resp.get("oos_ic"),
                "oos_rank_ic": resp.get("oos_rank_ic"),
                "hit_rate": resp.get("hit_rate"),
            },
        },
    }


@router.get("/ml/models")
async def ml_list_models(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    mrepo = MlModelRepository(db)
    rows = await mrepo.list_recent(limit=50)
    return {"code": "OK", "message": "ok", "data": rows}


@router.post("/ml/models/{model_id}/score")
async def ml_score_model(
    model_id: UUID,
    req: ScoreReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """用已保存模型对标的区间逐 bar 打分（标准化后线性加权）。"""
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    mrepo = MlModelRepository(db)
    model = await mrepo.get(model_id)
    if not model:
        raise NotFound("model_not_found")
    repo = KlineRepository(db)
    data = await score_with_model(
        repo,
        model["features"] or [],
        model["interval"],
        req.symbol.upper(),
        req.start,
        req.end,
    )
    return {"code": "OK", "message": "ok", "data": data}


@router.post("/research")
async def research(
    req: ResearchReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if req.end <= req.start:
        raise BadRequest("end_must_be_greater_than_start")
    if req.interval not in _INTERVALS:
        raise BadRequest("unsupported_interval")
    repo = KlineRepository(db)
    run_repo = FactorRunRepository(db)
    symbols = [s.upper() for s in req.symbols]
    extras = await _factor_extras(db)
    results = []
    for code in req.codes:
        result = await research_factor(repo, code, symbols, req.interval, req.start, req.end, extras=extras)
        if result.get("found"):
            decay = result.get("decay") or {}
            await run_repo.insert(
                code=code,
                symbol="|".join(symbols),
                interval=req.interval,
                start_time=req.start,
                end_time=req.end,
                ic=result.get("ic"),
                rank_ic=result.get("rank_ic"),
                decay={k: v for k, v in decay.items() if isinstance(v, (int, float))},
                n=result.get("n_total", 0),
            )
        results.append(result)
    return {"code": "OK", "message": "ok", "data": results}
