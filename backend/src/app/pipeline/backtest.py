"""流水线内存回测：用刚训练的模型 artifact 在回测窗口跑 ml_score（不落 backtests 表）。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..data.repository.kline_repo import KlineRepository
from ..strategy.engine.runner import BacktestConfig, run_backtest_vectorized


async def run_pipeline_backtest(
    repo: KlineRepository,
    *,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    artifact: dict[str, Any],
    training_start: datetime | None = None,
    window: int = 60,
    enter_q: float = 0.8,
    exit_q: float = 0.2,
) -> dict[str, Any]:
    """对刚训练的模型 artifact 做样本外回测。

    artifact 含 features / flip / booster（gbdt）。返回 {found, metrics, summary, equity_points, n_trades}。
    """
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start = _utc(start)
    end = _utc(end)
    if training_start is not None:
        training_start = _utc(training_start)

    interval_seconds = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
        "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
        "1M": 2592000,
    }.get(interval, 86400)
    lookback = max(int(window or 60) * 2, 120)
    feature_lookback = 0
    for feature in artifact.get("features") or []:
        expr = str(feature.get("expression") or "")
        values = [
            int(x)
            for x in re.findall(
                r"(?:rolling|shift|pct_change|ewm)\s*\(\s*(?:(?:window|periods|span)\s*=\s*)?(-?\d+)",
                expr,
            )
        ]
        feature_lookback = max(feature_lookback, sum(abs(x) for x in values))
    lookback = max(lookback, feature_lookback + int(window or 60))
    history_start = start - timedelta(seconds=interval_seconds * lookback)
    if artifact.get("feature_norm") == "expanding" and training_start is not None:
        history_start = min(history_start, training_start)

    rows = await repo.query_or_aggregate(symbol, interval, history_start, end)
    if not rows:
        return {"found": False, "message": "回测窗口内无数据（请先下载 K 线）"}
    df = pd.DataFrame(rows)
    df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 注入 agg:* 列（跨粒度聚合特征，口径与训练一致）
    agg_feats = [f for f in (artifact.get("features") or []) if f.get("aggregate")]
    if agg_feats:
        from ..factor.ml import _load_fine_agg

        for fi in {
            f.get("aggregate", {}).get("fine_interval")
            for f in agg_feats
            if f.get("aggregate", {}).get("fine_interval")
        }:
            agg_series = await _load_fine_agg(repo, symbol, str(fi), history_start, end, df.index)
            for code, s in agg_series.items():
                df[code] = s

    params: dict[str, Any] = {
        "features": artifact.get("features") or [],
        "_flip": float(artifact.get("flip", 1) or 1),
        "window": int(window or 60),
        "enter_q": float(enter_q or 0.8),
        "exit_q": float(exit_q or 0.2),
    }
    if artifact.get("model") == "gbdt":
        booster = artifact.get("booster")
        if booster is None:
            return {"found": False, "message": "gbdt 模型缺少 booster，无法回测"}
        params["_booster"] = booster

    cfg = BacktestConfig(initial_cash=100_000.0, fee_rate=0.001, allow_short=False)
    result = run_backtest_vectorized(
        ohlcv=df,
        template_id="ml_score",
        params=params,
        cfg=cfg,
        interval=interval,
        evaluation_start=start,
    )

    equity = result["equity"]
    step = max(1, len(equity) // 500)
    equity_points = [
        {"ts": pd.Timestamp(ts).to_pydatetime().isoformat(), "equity": round(float(v), 2)}
        for ts, v in equity.iloc[::step].items()
    ]
    metrics = dict(result["metrics"])

    # —— 基准：同区间买入持有（B&H），用于判断策略是否真有超额收益 ——
    eval_df = df.loc[df.index >= pd.Timestamp(start)]
    close = eval_df["close"].dropna()
    open_ = eval_df["open"].dropna()
    if len(close) >= 1 and len(open_) >= 1 and float(open_.iloc[0]) > 0:
        benchmark_start = float(open_.iloc[0])
        bench_equity = close / benchmark_start
        benchmark_return = float(bench_equity.iloc[-1] - 1)
        benchmark_mdd = float((bench_equity / bench_equity.cummax() - 1).min())
        metrics["benchmark_return"] = round(benchmark_return, 6)
        metrics["benchmark_max_drawdown"] = round(benchmark_mdd, 6)
        metrics["excess_return"] = round(
            float(metrics.get("total_return") or 0.0) - benchmark_return, 6
        )

    summary = {
        "symbol": symbol,
        "interval": interval,
        "metrics": metrics,
        "final_equity": float(equity.iloc[-1]) if len(equity) else None,
        "bars": len(equity),
        "params": {
            "window": int(window or 60),
            "enter_q": float(enter_q or 0.8),
            "exit_q": float(exit_q or 0.2),
        },
    }
    return {
        "found": True,
        "metrics": metrics,
        "summary": summary,
        "equity_points": equity_points,
        "n_trades": len(result["trades"]),
    }
