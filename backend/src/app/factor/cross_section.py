"""截面因子研究：同一时点「跨标的因子排名 vs 未来收益排名」的 IC / IR / t 值。

与 `service.research_factor`（时序 IC：单标的因子序列 vs 自身未来收益）的区别：
- 时序 IC 只用单标的自身历史，样本相关性强、易高估；
- 截面 IC 用**同一时点多个标的**做横截面排序，才是多资产因子投资的标准口径，
  广度（标的数）越大越能压低噪声、抬升可信 IC 上限。

数据要求：同一时点至少 `min_names` 个标的（默认 5）有因子值与未来收益。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..data.repository.kline_repo import KlineRepository
from .compute import compute_series, meta_or_extra

DEFAULT_MIN_NAMES = 5


async def _load_symbol_frame(
    repo: KlineRepository, symbol: str, interval: str, start: datetime, end: datetime
) -> pd.DataFrame | None:
    rows = await repo.query_or_aggregate(symbol, interval, start, end)
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(columns={"open_time": "time"}).set_index("time").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])
    return df if not df.empty else None


async def build_panels(
    repo: KlineRepository,
    code: str,
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int,
    extras: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], str | None]:
    """构建 (因子面板, 未来收益面板, 实际参与标的, 计算错误)。

    面板 index=时间、columns=标的；因子在 bar t 的值对应未来 horizon 根收益（无未来函数）。
    """
    fac: dict[str, pd.Series] = {}
    fwd: dict[str, pd.Series] = {}
    err: str | None = None
    meta = meta_or_extra(code, extras) or {}
    min_bars = (meta.get("min_bars") or 10) + 2
    for sym in symbols:
        df = await _load_symbol_frame(repo, sym, interval, start, end)
        if df is None or len(df) < min_bars:
            continue
        try:
            f = compute_series(code, df, extras)
        except (KeyError, ValueError) as exc:
            if err is None:
                err = str(exc)
            continue
        fac[sym] = pd.to_numeric(f, errors="coerce")
        fwd[sym] = df["close"].pct_change(periods=horizon, fill_method=None).shift(-horizon)
    if not fac:
        return pd.DataFrame(), pd.DataFrame(), [], err
    F = pd.DataFrame(fac).sort_index()
    R = pd.DataFrame(fwd).reindex(F.index)
    used = list(F.columns)
    return F, R, used, err


def cross_sectional_metrics(
    F: pd.DataFrame,
    R: pd.DataFrame,
    *,
    min_names: int = DEFAULT_MIN_NAMES,
    neutralize: bool = False,
    series_max: int = 400,
) -> dict[str, Any]:
    """逐时点算截面 IC/RankIC，聚合成均值/IR/t 值/胜率/多空价差。

    neutralize=True 时对因子与未来收益做**逐时点截面去均值**（等权市场中性）后再算 Pearson IC；
    RankIC 对截面去均值不敏感（排序不变），故仅影响 IC 口径。
    """
    ics: list[float] = []
    rics: list[float] = []
    spreads: list[float] = []
    counts: list[int] = []
    rows: list[dict[str, Any]] = []
    for t in F.index:
        frow = F.loc[t]
        rrow = R.loc[t]
        pair = pd.concat([frow, rrow], axis=1).dropna()
        if len(pair) < min_names:
            continue
        fv = pair.iloc[:, 0].astype(float)
        rv = pair.iloc[:, 1].astype(float)
        if fv.std() == 0 or rv.std() == 0:
            continue
        if neutralize:
            fv = fv - fv.mean()
            rv = rv - rv.mean()
        ic = float(fv.corr(rv))
        ric = float(fv.rank().corr(rv.rank()))
        if math.isnan(ic) or math.isnan(ric):
            continue
        # 多空价差：按因子分 5 组，取最高组与最低组的未来收益均值差（等权）
        q = min(5, len(pair))
        order = np.argsort(fv.to_numpy())
        grp = np.empty(len(pair), dtype=int)
        for gi in range(q):
            lo = round(len(pair) * gi / q)
            hi = round(len(pair) * (gi + 1) / q)
            grp[order[lo:hi]] = gi
        rvals = rv.to_numpy()
        spread = float(rvals[grp == q - 1].mean() - rvals[grp == 0].mean())
        ics.append(ic)
        rics.append(ric)
        spreads.append(spread)
        counts.append(len(pair))
        rows.append(
            {
                "ts": pd.Timestamp(t).to_pydatetime().isoformat(),
                "ic": round(ic, 5),
                "rank_ic": round(ric, 5),
                "spread": round(spread, 6),
                "n": len(pair),
            }
        )

    n = len(ics)
    if n == 0:
        return {
            "found": False,
            "message": f"截面样本不足：同一时点可用标的需 ≥ {min_names} 个（请先下载更多标的的 K 线）",
            "n_periods": 0,
        }

    def _agg(vals: list[float]) -> dict[str, float | None]:
        arr = np.asarray(vals, dtype=float)
        mean = float(arr.mean())
        std = float(arr.std(ddof=1)) if n > 1 else 0.0
        ir = mean / std if std > 0 else None
        tstat = ir * math.sqrt(n) if ir is not None else None
        pos = float(np.mean(arr > 0))
        return {
            "mean": round(mean, 5),
            "std": round(std, 5) if std else 0.0,
            "ir": round(ir, 3) if ir is not None else None,
            "t_stat": round(tstat, 2) if tstat is not None else None,
            "positive_ratio": round(pos, 4),
        }

    ic_agg = _agg(ics)
    ric_agg = _agg(rics)
    spread_mean = float(np.mean(spreads)) if spreads else None

    # 抽稀时序（前端画 IC 曲线）
    if len(rows) > series_max:
        idx = np.unique(np.round(np.linspace(0, len(rows) - 1, series_max)).astype(int))
        series = [rows[i] for i in idx]
    else:
        series = rows

    return {
        "found": True,
        "n_periods": n,
        "avg_names": round(float(np.mean(counts)), 2),
        "min_names": min_names,
        "neutralize": neutralize,
        "ic": ic_agg["mean"],
        "ic_std": ic_agg["std"],
        "ic_ir": ic_agg["ir"],
        "ic_t": ic_agg["t_stat"],
        "ic_positive_ratio": ic_agg["positive_ratio"],
        "rank_ic": ric_agg["mean"],
        "rank_ic_std": ric_agg["std"],
        "rank_ic_ir": ric_agg["ir"],
        "rank_ic_t": ric_agg["t_stat"],
        "rank_ic_positive_ratio": ric_agg["positive_ratio"],
        "long_short_spread": round(spread_mean, 6) if spread_mean is not None else None,
        "series": series,
    }


async def run_cs_backtest(
    repo: KlineRepository,
    code: str,
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    top_q: float = 0.2,
    cost_bps: float = 15.0,
    min_names: int = DEFAULT_MIN_NAMES,
    extras: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """截面多空回测：每 h 根按因子排序，做多头部 top_q、做空尾部 top_q，等权、扣成本。

    - 每 `horizon` 根调仓一次，持有 h 根（避免重叠持仓）；
    - 收益用 bar t 的因子分预测 t→t+h 的未来收益（无未来函数）；
    - 成本近似：每期双边换手 ≈ 2·top_q，乘以 cost_bps。
    返回净值曲线 + 指标（总收益/年化/夏普/最大回撤/胜率/多空毛收益）。
    """
    from ..strategy.engine.metrics import BARS_PER_YEAR

    meta = meta_or_extra(code, extras)
    if meta is None:
        return {"code": code, "found": False, "message": "unknown_factor"}
    F, R, used, err = await build_panels(repo, code, symbols, interval, start, end, horizon, extras)
    if F.empty or len(used) < 2:
        return {
            "code": code,
            "name": meta.get("name", code),
            "found": False,
            "message": err or f"可用标的不足（当前 {len(used)} 个）",
        }

    step = max(1, int(horizon))
    per: list[dict[str, Any]] = []
    for i in range(0, len(F.index) - 1, step):
        t = F.index[i]
        pair = pd.concat([F.loc[t], R.loc[t]], axis=1).dropna()
        if len(pair) < min_names:
            continue
        fv = pair.iloc[:, 0].to_numpy(dtype=float)
        rv = pair.iloc[:, 1].to_numpy(dtype=float)
        k = max(1, int(round(len(pair) * top_q)))
        order = np.argsort(fv)
        longs = order[-k:]
        shorts = order[:k]
        long_ret = float(rv[longs].mean())
        short_ret = float(rv[shorts].mean())
        gross = long_ret - short_ret
        cost = (2.0 * top_q) * (float(cost_bps) / 1e4)
        per.append(
            {
                "ts": pd.Timestamp(t).to_pydatetime().isoformat(),
                "gross": round(gross, 6),
                "net": round(gross - cost, 6),
                "long": round(long_ret, 6),
                "short": round(short_ret, 6),
                "n": len(pair),
            }
        )
    if not per:
        return {
            "code": code,
            "name": meta.get("name", code),
            "found": False,
            "message": f"每期可用标的需 ≥ {min_names} 个（请先下载更多标的）",
        }

    rets = np.asarray([p["net"] for p in per], dtype=float)
    equity = np.cumprod(1.0 + rets)
    total = float(equity[-1] - 1.0)
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    ppy = float(BARS_PER_YEAR.get(interval, 365.0)) / step
    sharpe = float(rets.mean() / std * math.sqrt(ppy)) if std > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    mdd = float((equity / peak - 1.0).min())
    win = float(np.mean(rets > 0))
    periods = max(1, len(equity) - 1)
    ann = float(equity[-1] ** (ppy / periods) - 1.0) if equity[-1] > 0 else -1.0

    # 抽稀净值点
    if len(per) > 500:
        idx = np.unique(np.round(np.linspace(0, len(per) - 1, 500)).astype(int))
    else:
        idx = np.arange(len(per))
    equity_points = [
        {"ts": per[i]["ts"], "equity": round(float(equity[i]), 4)} for i in idx
    ]

    return {
        "code": code,
        "name": meta.get("name", code),
        "found": True,
        "mode": "cross_sectional_backtest",
        "interval": interval,
        "symbols": used,
        "horizon": horizon,
        "top_q": top_q,
        "cost_bps": cost_bps,
        "n_periods": len(per),
        "avg_names": round(float(np.mean([p["n"] for p in per])), 2),
        "metrics": {
            "total_return": round(total, 6),
            "annual_return": round(ann, 6),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(mdd, 6),
            "win_rate": round(win, 4),
            "num_trades": len(per),
            "gross_spread_mean": round(float(np.mean([p["gross"] for p in per])), 6),
        },
        "equity_points": equity_points,
        "series": per[:500],
    }


async def research_factor_cs(
    repo: KlineRepository,
    code: str,
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    min_names: int = DEFAULT_MIN_NAMES,
    neutralize: bool = False,
    extras: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """单因子截面研究。返回 code/name/指标 + 逐时点 IC 序列。"""
    meta = meta_or_extra(code, extras)
    if meta is None:
        return {"code": code, "mode": "cross_sectional", "found": False, "message": "unknown_factor"}

    F, R, used, err = await build_panels(repo, code, symbols, interval, start, end, horizon, extras)
    base: dict[str, Any] = {
        "code": code,
        "name": meta.get("name", code),
        "mode": "cross_sectional",
        "interval": interval,
        "symbols": used,
        "requested_symbols": symbols,
        "horizon": horizon,
    }
    if F.empty or len(used) < 2:
        base.update(
            {
                "found": False,
                "message": err or f"可用标的不足（需 ≥2 个有数据的标的，当前 {len(used)} 个）",
            }
        )
        return base
    metrics = cross_sectional_metrics(F, R, min_names=min_names, neutralize=neutralize)
    base.update(metrics)
    return base
