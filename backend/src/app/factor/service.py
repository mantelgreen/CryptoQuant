"""因子研究 v0：对本地 K 线计算因子序列并度量 IC / RankIC / 衰减。

口径：因子在 bar t 的值，对应当前 bar 收盘 → 未来 1..H 根 K 线收益（样本外无未来函数）。
IC = pearson(factor, fwd_ret)，RankIC = 二者 rank 的 pearson；
多标的时按标的先算再等权平均。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..data.repository.kline_repo import KlineRepository
from .registry import compute_factor, get_factors
from .compute import compute_series, meta_or_extra
from .sandbox import SandboxError, safe_eval_expr
HORIZONS = (1, 3, 5)


async def _load_frame(repo: KlineRepository, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = await repo.query_or_aggregate(symbol, interval, start, end)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if a.nunique() < 3 or b.nunique() < 3:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def _single_metrics(factor: pd.Series, close: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS:
        fwd = close.pct_change(periods=h, fill_method=None).shift(-h)
        pair = pd.concat([factor, fwd], axis=1).dropna()
        if len(pair) < 30:
            out[f"ic_{h}"] = None
            continue
        fac = pair.iloc[:, 0]
        ret = pair.iloc[:, 1]
        ic = float(fac.corr(ret)) if ret.std() > 0 and fac.std() > 0 else float("nan")
        out[f"ic_{h}"] = round(ic, 5) if not math.isnan(ic) else None
    return out


async def research_factor(
    repo: KlineRepository,
    code: str,
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    extras: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对单个因子做跨标的研究，返回聚合 IC/RankIC 与衰减。支持内置与用户表达式因子。"""
    meta = meta_or_extra(code, extras)
    if meta is None:
        return {"code": code, "error": "unknown_factor"}

    per_symbol: list[dict[str, Any]] = []
    agg_ic: list[float] = []
    agg_rank: list[float] = []
    decay_rows: dict[int, list[float | None]] = {h: [] for h in HORIZONS}
    total_n = 0
    compute_error: str | None = None

    for sym in symbols:
        df = await _load_frame(repo, sym, interval, start, end)
        if df.empty or len(df) < meta["min_bars"] + 2:
            continue
        try:
            factor = compute_series(code, df, extras)
        except (KeyError, SandboxError, ValueError) as exc:
            if compute_error is None:
                compute_error = str(exc)
            continue
        close = df["close"]
        ic = _single_metrics(factor, close)

        fwd = close.pct_change(fill_method=None).shift(-1)
        pair = pd.concat([factor, fwd], axis=1).dropna()
        sym_result: dict[str, Any] = {"symbol": sym, "n": int(len(pair))}
        total_n += len(pair)
        if len(pair) >= 30:
            fac = pair.iloc[:, 0]
            ret = pair.iloc[:, 1]
            if ret.std() > 0 and fac.std() > 0:
                p_ic = float(fac.corr(ret))
                r_ic = _spearman(fac, ret)
                if not math.isnan(p_ic):
                    agg_ic.append(p_ic)
                    sym_result["ic"] = round(p_ic, 5)
                if not math.isnan(r_ic):
                    agg_rank.append(r_ic)
                    sym_result["rank_ic"] = round(r_ic, 5)
        else:
            sym_result["note"] = "样本不足(<30)"
        for h in HORIZONS:
            if ic.get(f"ic_{h}") is not None:
                decay_rows[h].append(ic[f"ic_{h}"])
        per_symbol.append(sym_result)

    if not per_symbol:
        return {
            "code": code,
            "name": meta["name"],
            "interval": interval,
            "symbols": symbols,
            "found": False,
            "message": compute_error or "窗口内无足够数据（请先下载 K 线）",
            "per_symbol": [],
        }

    def _avg(vals: list[float]) -> float | None:
        return round(float(np.mean(vals)), 5) if vals else None

    return {
        "code": code,
        "name": meta["name"],
        "tags": meta["tags"],
        "interval": interval,
        "symbols": symbols,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "found": True,
        "ic": _avg(agg_ic),
        "rank_ic": _avg(agg_rank),
        "decay": {f"h_{h}": _avg([v for v in decay_rows[h] if v is not None]) for h in HORIZONS},
        "n_total": total_n,
        "per_symbol": per_symbol,
    }


async def matrix_factor(
    repo: KlineRepository,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    extras: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """多因子相关矩阵：在每个标的上对齐计算各因子序列，取相关矩阵再跨标的等权平均。

    附带每因子的 IC/RankIC（相对下一根收益），用于冗余因子筛选。支持内置与用户表达式因子。
    """
    valid = [c for c in codes if meta_or_extra(c, extras) is not None]
    if not valid:
        return {"found": False, "message": "没有可用的因子代码", "factors": [], "corr": []}

    corr_sum: pd.DataFrame | None = None
    corr_cnt: pd.DataFrame | None = None
    ic_rows: dict[str, list[float]] = {c: [] for c in valid}
    rank_rows: dict[str, list[float]] = {c: [] for c in valid}

    for sym in symbols:
        df = await _load_frame(repo, sym, interval, start, end)
        if df.empty:
            continue
        series: dict[str, pd.Series] = {}
        for code in valid:
            meta = meta_or_extra(code, extras) or {}
            if len(df) < (meta.get("min_bars") or 10) + 2:
                continue
            try:
                series[code] = compute_series(code, df, extras)
            except (KeyError, SandboxError, ValueError):
                continue
        if len(series) < 2:
            continue
        fdf = pd.DataFrame(series).dropna()
        if len(fdf) < 30:
            continue
        cm = fdf.corr()
        cm_cnt = cm.notna().astype(int)
        corr_sum = cm if corr_sum is None else corr_sum.add(cm, fill_value=0.0)
        corr_cnt = cm_cnt if corr_cnt is None else corr_cnt.add(cm_cnt, fill_value=0)

        fwd = df["close"].pct_change(fill_method=None).shift(-1)
        for code, col in series.items():
            pair = pd.concat([col, fwd], axis=1).dropna()
            if len(pair) < 30:
                continue
            fac, ret = pair.iloc[:, 0], pair.iloc[:, 1]
            if ret.std() > 0 and fac.std() > 0:
                p_ic = float(fac.corr(ret))
                r_ic = _spearman(fac, ret)
                if not math.isnan(p_ic):
                    ic_rows[code].append(p_ic)
                if not math.isnan(r_ic):
                    rank_rows[code].append(r_ic)

    if corr_sum is None or corr_cnt is None:
        return {"found": False, "message": "窗口内数据不足，无法计算相关矩阵", "factors": [], "corr": []}

    corr_avg = corr_sum.div(corr_cnt.where(corr_cnt > 0)).reindex(index=valid, columns=valid)
    corr = corr_avg.fillna(0.0).round(5).to_numpy().tolist()
    factors = [
        {
            "code": code,
            "name": (meta_or_extra(code, extras) or {}).get("name", code),
            "ic": round(float(np.mean(ic_rows[code])), 5) if ic_rows[code] else None,
            "rank_ic": round(float(np.mean(rank_rows[code])), 5) if rank_rows[code] else None,
        }
        for code in valid
    ]
    return {"found": True, "interval": interval, "symbols": symbols, "factors": factors, "corr": corr}


async def research_expression(
    repo: KlineRepository,
    expression: str,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """AI/手写因子表达式：AST 白名单校验后在本地数据上实测 IC（不落库）。"""
    df = await _load_frame(repo, symbol, interval, start, end)
    if df.empty:
        return {"found": False, "expression": expression, "message": "窗口内无数据（请先下载 K 线）"}
    try:
        factor = safe_eval_expr(expression, df)
    except SandboxError as exc:
        return {"found": False, "expression": expression, "ok": False, "errors": [str(exc)]}
    close = df["close"]
    metrics = _single_metrics(factor, close)
    fwd = close.pct_change(fill_method=None).shift(-1)
    pair = pd.concat([factor, fwd], axis=1).dropna()
    if len(pair) < 30:
        return {
            "found": True,
            "expression": expression,
            "ok": True,
            "ic": None,
            "rank_ic": None,
            "decay": {f"h_{h}": None for h in HORIZONS},
            "n_total": int(len(pair)),
            "message": "样本不足(<30)",
        }
    fac, ret = pair.iloc[:, 0], pair.iloc[:, 1]
    if fac.std() < 1e-12:
        return {
            "found": True,
            "expression": expression,
            "ok": True,
            "ic": None,
            "rank_ic": None,
            "decay": {f"h_{h}": None for h in HORIZONS},
            "n_total": int(len(pair)),
            "message": "因子在窗口内几乎恒定（标准差≈0），无区分度——请加入动态成分（如滚动/差分/动量）",
        }
    p_ic = float(fac.corr(ret)) if ret.std() > 0 else float("nan")
    r_ic = _spearman(fac, ret)
    decay = {f"h_{h}": metrics.get(f"ic_{h}") for h in HORIZONS}
    message = None
    if math.isnan(r_ic):
        uniq = int(fac.nunique())
        message = (
            f"RankIC 未计算：因子只有 {uniq} 个不同取值（二值/低离散信号），"
            "秩相关无法区分排序。建议改用连续取值（如动量幅度、乖离率、量比）以获得有意义的 RankIC。"
        )
    return {
        "found": True,
        "expression": expression,
        "ok": True,
        "symbol": symbol,
        "interval": interval,
        "ic": round(p_ic, 5) if not math.isnan(p_ic) else None,
        "rank_ic": round(r_ic, 5) if not math.isnan(r_ic) else None,
        "decay": decay,
        "n_total": int(len(pair)),
        "message": message,
    }

# =============================================================================
# 目录 / 用户因子 / 研究落库 / ML 模型 —— 面向“服务层可打包”的持久化编排服务
# API 层只做参数解析与 HTTP 错误映射，业务与 SQL 都收敛在下面这些函数里。
# =============================================================================

async def build_factor_extras(db) -> dict[str, dict[str, Any]]:
    """用户/AI 表达式因子：code(id) -> {name, description, tags, min_bars, expression}。"""
    from .repo import UserFactorRepository

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


async def build_extras_for_codes(db, codes: list[str]) -> dict[str, dict[str, Any]]:
    """在用户因子基础上，把列表里“非内置、非已注册”的 code 按表达式兜底，
    让尚未入库的 AI 草稿（code 即原始表达式）也能参与研究/训练。"""
    extras = await build_factor_extras(db)
    builtin = set(get_factors())
    for code in codes:
        c = code.strip()
        if not c or len(c) > 2000 or code in builtin or code in extras:
            continue
        extras[code] = {
            "name": f"expr:{c[:20]}",
            "description": "",
            "tags": ["AI"],
            "min_bars": 0,
            "expression": c,
            "is_user": True,
        }
    return extras


async def build_factor_name_map(db) -> dict[str, str]:
    """code → 可读因子名（内置 + 用户/AI 因子）。"""
    from .repo import UserFactorRepository

    names = {code: meta.get("name", code) for code, meta in get_factors().items()}
    for r in await UserFactorRepository(db).list():
        names[str(r["id"])] = r["name"]
    return names


async def list_factor_catalog(db) -> list[dict[str, Any]]:
    """内置因子 + 用户/AI 生成因子（表达式因子，带 is_user/expression）。"""
    from .repo import UserFactorRepository

    builtins = [
        {"code": code, **{k: v for k, v in meta.items() if k != "fn"}}
        for code, meta in get_factors().items()
    ]
    user_items = []
    for r in await UserFactorRepository(db).list():
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
    return [*builtins, *user_items]


async def join_user_factor(
    db,
    *,
    name: str,
    description: str,
    expression: str,
    source_model: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """把草稿（AI/手动）正式加入因子库（draft 状态）。"""
    from .repo import UserFactorRepository

    fid = await UserFactorRepository(db).create(
        name=name.strip(),
        description=description.strip(),
        expression=expression.strip(),
        source_model=source_model,
        tags=tags or ["AI"],
    )
    return str(fid)


async def tag_quality_factors(db, codes: list[str], tag: str = "优质") -> int:
    """把流水线验证通过的优质因子打上标签（在因子库凸显）。返回成功打标的因子数。"""
    from uuid import UUID

    from .repo import UserFactorRepository

    repo = UserFactorRepository(db)
    done = 0
    for code in codes:
        try:
            await repo.add_tag(UUID(str(code)), tag)
            done += 1
        except (ValueError, TypeError):
            continue
    return done


async def check_user_factor_usage(db, factor_id) -> dict[str, Any]:
    """用户因子被谁引用（删除前置检查）：保存过的模型 / 策略版本。"""
    from sqlalchemy import select

    from ..data.models.orm import ml_models_table, strategies_table, strategy_versions_table

    uid = str(factor_id)
    ml, sv, st = ml_models_table(), strategy_versions_table(), strategies_table()

    mres = await db.execute(select(ml.c.id, ml.c.name).where(ml.c.features.contains([{"code": uid}])))
    models = [{"id": str(i), "name": n} for i, n in mres]

    vres = await db.execute(select(sv.c.strategy_id).where(sv.c.params.contains({"features": [{"code": uid}]})))
    strategy_ids = {r[0] for r in vres}
    strategies: list[dict[str, Any]] = []
    if strategy_ids:
        sres = await db.execute(select(st.c.id, st.c.name).where(st.c.id.in_(strategy_ids)))
        strategies = [{"id": str(i), "name": n} for i, n in sres]
    return {"models": models, "strategies": strategies}


async def delete_user_factor(db, factor_id) -> bool:
    from .repo import UserFactorRepository

    return await UserFactorRepository(db).delete(factor_id)


async def run_factor_matrix(
    db,
    *,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    repo = KlineRepository(db)
    extras = await build_extras_for_codes(db, codes)
    return await matrix_factor(repo, codes, symbols, interval, start, end, extras=extras)


async def verify_factor_expression(
    db,
    *,
    expression: str,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    repo = KlineRepository(db)
    return await research_expression(repo, expression, symbol, interval, start, end)


async def run_factor_research_batch(
    db,
    *,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """批量研究并对“有结果”的因子写 IC 记录（供历史回看）。"""
    from .repo import FactorRunRepository

    repo = KlineRepository(db)
    run_repo = FactorRunRepository(db)
    extras = await build_extras_for_codes(db, codes)
    results = []
    for code in codes:
        result = await research_factor(repo, code, symbols, interval, start, end, extras=extras)
        if result.get("found"):
            decay = result.get("decay") or {}
            await run_repo.insert(
                code=code,
                symbol="|".join(symbols),
                interval=interval,
                start_time=start,
                end_time=end,
                ic=result.get("ic"),
                rank_ic=result.get("rank_ic"),
                decay={k: v for k, v in decay.items() if isinstance(v, (int, float))},
                n=result.get("n_total", 0),
            )
        results.append(result)
    return results


async def train_factor_model(
    db,
    *,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    test_ratio: float = 0.25,
    val_ratio: float = 0.0,
    alpha: float = 1.0,
    model: str = "ridge",
    gbdt_objective: str = "huber",
    max_corr: float | None = 0.8,
    winsor: float | None = None,
    feature_norm: str = "global",
    cost_bps: float = 0.0,
    fine_interval: str | None = None,
    agg_features: list[str] | None = None,
    learning_rate: float = 0.05,
    n_estimators: int = 600,
    max_depth: int = 3,
    num_leaves: int = 7,
    min_child_samples: int = 20,
    seed_ensemble: int = 3,
    subsample: float = 0.9,
    colsample_bytree: float = 0.9,
) -> dict[str, Any]:
    from .ml import run_ml_research

    repo = KlineRepository(db)
    extras = await build_extras_for_codes(db, codes)
    return await run_ml_research(
        repo,
        codes,
        symbols,
        interval,
        start,
        end,
        horizon=horizon,
        test_ratio=test_ratio,
        val_ratio=val_ratio,
        alpha=alpha,
        max_corr=max_corr,
        winsor=winsor,
        feature_norm=feature_norm,
        cost_bps=cost_bps,
        fine_interval=fine_interval,
        agg_features=agg_features,
        model=model,
        learning_rate=learning_rate,
        n_estimators=n_estimators,
        max_depth=max_depth,
        num_leaves=num_leaves,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
        extras=extras,
    )


async def train_factor_model_full(
    db,
    *,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    test_ratio: float = 0.25,
    val_ratio: float = 0.0,
    alpha: float = 1.0,
    model: str = "ridge",
    gbdt_objective: str = "huber",
    max_corr: float | None = 0.8,
    winsor: float | None = None,
    feature_norm: str = "global",
    cost_bps: float = 0.0,
    fine_interval: str | None = None,
    agg_features: list[str] | None = None,
    learning_rate: float = 0.05,
    n_estimators: int = 600,
    max_depth: int = 3,
    num_leaves: int = 7,
    min_child_samples: int = 20,
    seed_ensemble: int = 3,
    subsample: float = 0.9,
    colsample_bytree: float = 0.9,
) -> dict[str, Any]:
    """训练多因子模型并返回含 artifact 的完整结果（供流水线回测/入库复用，避免二次训练）。"""
    from .ml import run_ml_research

    repo = KlineRepository(db)
    extras = await build_extras_for_codes(db, codes)
    return await run_ml_research(
        repo,
        codes,
        symbols,
        interval,
        start,
        end,
        horizon=horizon,
        test_ratio=test_ratio,
        val_ratio=val_ratio,
        alpha=alpha,
        max_corr=max_corr,
        winsor=winsor,
        feature_norm=feature_norm,
        cost_bps=cost_bps,
        fine_interval=fine_interval,
        agg_features=agg_features,
        model=model,
        gbdt_objective=gbdt_objective,
        learning_rate=learning_rate,
        n_estimators=n_estimators,
        max_depth=max_depth,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
        seed_ensemble=seed_ensemble,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
        extras=extras,
        return_artifact=True,
    )


async def persist_model_artifact(
    db,
    *,
    name: str,
    model: str,
    interval: str,
    horizon: int,
    symbols: list[str],
    artifact: dict[str, Any],
    resp: dict[str, Any],
    start: datetime,
    end: datetime,
    test_ratio: float,
    winsor: float | None,
    cost_bps: float,
) -> dict[str, Any]:
    """把已训练好的 artifact 落库（不重训）。ridge 落线性权重；gbdt 序列化到磁盘，DB 记 model_path。"""
    from uuid import uuid4

    from .ml import save_model_pickle

    from .repo import MlModelRepository

    mid = None
    model_path = None
    if artifact.get("model") == "gbdt":
        booster = artifact.get("booster")
        if booster is None:
            raise ValueError("LightGBM 训练产物缺失，无法保存")
        mid = uuid4()
        model_path = save_model_pickle(mid, booster)
    mrepo = MlModelRepository(db)
    saved_id = await mrepo.create(
        name=name,
        model_type=model,
        interval=interval,
        horizon=horizon,
        symbols=symbols,
        features=artifact["features"],
        metrics={
            "oos_ic": resp.get("oos_ic"),
            "oos_rank_ic": resp.get("oos_rank_ic"),
            "hit_rate": resp.get("hit_rate"),
            "spread": resp.get("spread"),
            "val_ic": resp.get("val_ic"),
            "val_rank_ic": resp.get("val_rank_ic"),
            "val_hit_rate": resp.get("val_hit_rate"),
            "val_spread": resp.get("val_spread"),
        },
        n_train=resp.get("n_train", 0),
        n_test=resp.get("n_test", 0),
        mid=mid,
        model_path=model_path,
        train_start=start,
        train_end=end,
        dataset={
            "symbols": resp.get("symbols") or symbols,
            "interval": interval,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "test_ratio": test_ratio,
            "val_ratio": resp.get("val_ratio", 0.0),
            "horizon": horizon,
            "winsor": winsor,
            "cost_bps": cost_bps,
            "fine_interval": resp.get("fine_interval"),
            "agg_features": resp.get("agg_features") or [],
            "n_train": resp.get("n_train", 0),
            "n_test": resp.get("n_test", 0),
            "n_val": resp.get("n_val", 0),
            "flip": resp.get("flip", 1.0),
            "test_windows": resp.get("test_windows") or [],
        },
    )
    return {
        "model_id": str(saved_id),
        "name": name,
        "model_type": model,
        "interval": interval,
        "horizon": horizon,
        "features": len(artifact["features"]),
        "metrics": {
            "oos_ic": resp.get("oos_ic"),
            "oos_rank_ic": resp.get("oos_rank_ic"),
            "hit_rate": resp.get("hit_rate"),
        },
    }


async def persist_trained_model(
    db,
    *,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    name: str,
    horizon: int = 1,
    test_ratio: float = 0.25,
    val_ratio: float = 0.0,
    alpha: float = 1.0,
    model: str = "ridge",
    gbdt_objective: str = "huber",
    max_corr: float | None = 0.8,
    winsor: float | None = None,
    feature_norm: str = "global",
    cost_bps: float = 0.0,
    fine_interval: str | None = None,
    agg_features: list[str] | None = None,
    learning_rate: float = 0.05,
    n_estimators: int = 600,
    max_depth: int = 3,
    num_leaves: int = 7,
    min_child_samples: int = 20,
    seed_ensemble: int = 3,
    subsample: float = 0.9,
    colsample_bytree: float = 0.9,
) -> dict[str, Any]:
    """训练并把模型 artifact 落库：ridge 落线性权重；gbdt 序列化到磁盘，DB 记 model_path。"""
    resp = await train_factor_model_full(
        db,
        codes=codes,
        symbols=symbols,
        interval=interval,
        start=start,
        end=end,
        horizon=horizon,
        test_ratio=test_ratio,
        val_ratio=val_ratio,
        alpha=alpha,
        max_corr=max_corr,
        winsor=winsor,
        feature_norm=feature_norm,
        cost_bps=cost_bps,
        fine_interval=fine_interval,
        agg_features=agg_features,
        model=model,
        gbdt_objective=gbdt_objective,
        learning_rate=learning_rate,
        n_estimators=n_estimators,
        max_depth=max_depth,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
        seed_ensemble=seed_ensemble,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
    )
    if not resp.get("found") or "artifact" not in resp:
        raise ValueError(resp.get("message") or "训练失败，无法保存")
    return await persist_model_artifact(
        db,
        name=name,
        model=model,
        interval=interval,
        horizon=horizon,
        symbols=symbols,
        artifact=resp["artifact"],
        resp=resp,
        start=start,
        end=end,
        test_ratio=test_ratio,
        winsor=winsor,
        cost_bps=cost_bps,
    )


async def list_saved_models(db, limit: int = 50) -> list[dict[str, Any]]:
    from .repo import MlModelRepository

    rows = await MlModelRepository(db).list_recent(limit=limit)
    names = await build_factor_name_map(db)
    for row in rows:
        for f in row.get("features") or []:
            code = f.get("code")
            if not code:
                continue
            if f.get("name"):
                continue
            name = names.get(code)
            f["name"] = name or (f"expr:{(f.get('expression') or '')[:18]}" if f.get("expression") else code)
    return rows


def _trained_on(model: dict[str, Any], symbol: str) -> bool:
    return symbol in (model.get("symbols") or [])


def _excluded_score_start(model: dict[str, Any], symbol: str, start: datetime) -> tuple[datetime, datetime | None]:
    """样本排除：若评估区间与模型训练窗口重叠，把起点抬到训练窗口之后。"""
    train_end = model.get("train_end")
    if train_end and _trained_on(model, symbol) and start < train_end:
        return train_end, train_end
    return start, None


async def score_saved_model(db, model_id, symbol: str, start: datetime, end: datetime) -> dict[str, Any] | None:
    """用已保存模型打分（含样本外排除）。模型不存在返回 None。"""
    from .ml import score_with_model

    from .repo import MlModelRepository

    mrepo = MlModelRepository(db)
    model = await mrepo.get(model_id)
    if not model:
        return None
    symbol = symbol.upper()
    start, excluded_to = _excluded_score_start(model, symbol, start)
    if start >= end:
        train_end = model.get("train_end")
        raise ValueError(
            f"评估窗口与训练数据区间重叠已被样本排除（该标的训练至 {train_end:%Y-%m-%d %H:%M}），"
            "窗口内已无样本可打分；请选择训练结束之后的区间。"
        )
    repo = KlineRepository(db)
    data = await score_with_model(repo, model, model["interval"], symbol, start, end)
    if data.get("found") and excluded_to is not None:
        data["train_window_excluded_to"] = excluded_to.isoformat()
    return data


async def check_model_usage(db, model_id) -> dict[str, Any] | None:
    """模型被哪些策略 / 模拟盘自动账户引用（绑定检查）。模型不存在返回 None。"""
    from sqlalchemy import select

    from ..data.models.orm import sim_accounts_table, strategies_table, strategy_versions_table

    from .repo import MlModelRepository

    if not await MlModelRepository(db).get(model_id):
        return None

    uid = str(model_id)
    sv, st, sa = strategy_versions_table(), strategies_table(), sim_accounts_table()

    vres = await db.execute(select(sv.c.strategy_id).where(sv.c.params.contains({"model_id": uid})))
    strategy_ids = {r[0] for r in vres}
    strategies: list[dict[str, Any]] = []
    if strategy_ids:
        sres = await db.execute(select(st.c.id, st.c.name).where(st.c.id.in_(strategy_ids)))
        strategies = [{"id": str(i), "name": n} for i, n in sres]

    ares = await db.execute(select(sa.c.id, sa.c.name).where(sa.c.config.contains({"model_id": uid})))
    sim_accounts = [{"id": str(i), "name": n} for i, n in ares]
    return {"strategies": strategies, "sim_accounts": sim_accounts}


async def delete_saved_model(db, model_id) -> dict[str, Any] | None:
    """删除模型（行 + 磁盘 pickle artifact）。模型不存在返回 None。"""
    from pathlib import Path

    from .ml import model_storage_dir

    from .repo import MlModelRepository

    mrepo = MlModelRepository(db)
    model = await mrepo.get(model_id)
    if not model:
        return None
    deleted = await mrepo.delete(model_id)
    mp = model.get("model_path")
    if mp and deleted:
        try:
            (model_storage_dir() / Path(mp).name).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return {"deleted": deleted, "name": model.get("name")}

async def latest_factor_runs(db, limit: int = 200) -> list[dict[str, Any]]:
    from .repo import FactorRunRepository

    return await FactorRunRepository(db).latest(limit=limit)


async def factor_run_history(db, code: str, limit: int = 30) -> list[dict[str, Any]]:
    from .repo import FactorRunRepository

    return await FactorRunRepository(db).by_code(code, limit=limit)
