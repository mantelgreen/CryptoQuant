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
    rows = await repo.query_or_aggregate(symbol, interval, start, end, limit=50_000)
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
        fwd = close.pct_change(periods=h).shift(-h)
        pair = pd.concat([factor, fwd], axis=1).dropna()
        if len(pair) < 30:
            out[f"ic_{h}"] = None
            continue
        fac = pair.iloc[:, 0]
        ret = pair.iloc[:, 1]
        ic = float(fac.corr(ret)) if ret.std() > 0 else float("nan")
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

    for sym in symbols:
        df = await _load_frame(repo, sym, interval, start, end)
        if df.empty or len(df) < meta["min_bars"] + 2:
            continue
        try:
            factor = compute_series(code, df, extras)
        except (KeyError, SandboxError, ValueError):
            continue
        close = df["close"]
        ic = _single_metrics(factor, close)

        fwd = close.pct_change().shift(-1)
        pair = pd.concat([factor, fwd], axis=1).dropna()
        sym_result: dict[str, Any] = {"symbol": sym, "n": int(len(pair))}
        if len(pair) >= 30:
            fac = pair.iloc[:, 0]
            ret = pair.iloc[:, 1]
            if ret.std() > 0:
                p_ic = float(fac.corr(ret))
                r_ic = _spearman(fac, ret)
                if not math.isnan(p_ic):
                    agg_ic.append(p_ic)
                    sym_result["ic"] = round(p_ic, 5)
                if not math.isnan(r_ic):
                    agg_rank.append(r_ic)
                    sym_result["rank_ic"] = round(r_ic, 5)
            total_n += len(pair)
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
            "message": "窗口内无足够数据（请先下载 K 线）",
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

    corr_sum: np.ndarray | None = None
    corr_n = 0
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
        cm = fdf.corr().to_numpy()
        corr_sum = cm if corr_sum is None else corr_sum + cm
        corr_n += 1

        fwd = df["close"].pct_change().shift(-1)
        for code, col in series.items():
            pair = pd.concat([col, fwd], axis=1).dropna()
            if len(pair) < 30:
                continue
            fac, ret = pair.iloc[:, 0], pair.iloc[:, 1]
            if ret.std() > 0:
                p_ic = float(fac.corr(ret))
                r_ic = _spearman(fac, ret)
                if not math.isnan(p_ic):
                    ic_rows[code].append(p_ic)
                if not math.isnan(r_ic):
                    rank_rows[code].append(r_ic)

    if corr_sum is None or corr_n == 0:
        return {"found": False, "message": "窗口内数据不足，无法计算相关矩阵", "factors": [], "corr": []}

    corr = (corr_sum / corr_n).round(5).tolist()
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
    fwd = close.pct_change().shift(-1)
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
    }
