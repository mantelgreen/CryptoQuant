"""绩效指标计算（纯函数，输入为净值 pandas.Series / 成交列表）。

参考：米筐 / QuantConnect 的绩效口径。所有比率基于 bar 频率换算。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ...core.json_sanitize import json_safe

# 每年 bar 数（近似），按颗粒度换算年化
BARS_PER_YEAR: dict[str, float] = {
    "1m": 365 * 24 * 60,
    "5m": 365 * 24 * 12,
    "15m": 365 * 24 * 4,
    "30m": 365 * 48,
    "1h": 365 * 24,
    "2h": 365 * 12,
    "4h": 365 * 6,
    "6h": 365 * 4,
    "12h": 365 * 2,
    "1d": 365,
    "3d": 365 / 3,
    "1w": 52,
    "1M": 12,
}


def _annual_factor(interval: str) -> float:
    return BARS_PER_YEAR.get(interval, 365.0)


def equity_to_returns(equity: pd.Series, *, log: bool = False) -> pd.Series:
    """净值 → 收益序列。log=True 返回对数收益。"""
    rets = equity.pct_change().dropna()
    if log:
        rets = np.log(equity / equity.shift(1)).dropna()
    return rets.replace([np.inf, -np.inf], np.nan).dropna()


def total_return(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def annual_return(equity: pd.Series, interval: str) -> float:
    """年化收益（复合）。"""
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    periods = max(len(equity) - 1, 1)
    total = float(equity.iloc[-1] / equity.iloc[0])
    if total <= 0:
        return -1.0
    return float(total ** (_annual_factor(interval) / periods) - 1)


def volatility(returns: pd.Series, interval: str) -> float:
    """年化波动率。"""
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=1) * np.sqrt(_annual_factor(interval)))


def sharpe_ratio(returns: pd.Series, interval: str, risk_free: float = 0.0) -> float:
    """夏普比率（无风险 0，年化）。"""
    if returns.empty or returns.std(ddof=1) == 0:
        return 0.0
    excess = returns.mean() * _annual_factor(interval) - risk_free
    ann_vol = returns.std(ddof=1) * np.sqrt(_annual_factor(interval))
    return float(excess / ann_vol) if ann_vol > 0 else 0.0


def sortino_ratio(returns: pd.Series, interval: str, risk_free: float = 0.0) -> float:
    """索提诺比率：仅把下行波动作为分母。"""
    downside = returns[returns < 0]
    if downside.empty:
        return np.inf if returns.mean() > 0 else 0.0
    downside_std = downside.std(ddof=1) if len(downside) > 1 else float(downside.abs().mean())
    if downside_std == 0:
        return 0.0
    excess = returns.mean() * _annual_factor(interval) - risk_free
    return float(excess / (downside_std * np.sqrt(_annual_factor(interval))))


def calmar_ratio(returns: pd.Series, equity: pd.Series, interval: str) -> float:
    """Calmar：年化收益 / 最大回撤绝对值。"""
    ann = annual_return(equity, interval)
    mdd = max_drawdown(equity)
    return float(ann / abs(mdd)) if mdd < 0 else 0.0


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（负值）。"""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1
    return float(dd.min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    """回撤序列。"""
    if equity.empty:
        return equity
    return equity / equity.cummax() - 1


def win_rate(trades: list[dict[str, Any]]) -> float:
    """胜率（按成交 pnl > 0）。"""
    if not trades:
        return 0.0
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return float(wins / len(pnls))


def profit_factor(trades: list[dict[str, Any]]) -> float:
    """盈亏比：总盈利 / 总亏损绝对值。无亏损时原始比值为 inf。"""
    if not trades:
        return 0.0
    gross_profit = sum(float(t.get("pnl", 0) or 0) for t in trades if float(t.get("pnl", 0) or 0) > 0)
    gross_loss = sum(abs(float(t.get("pnl", 0) or 0)) for t in trades if float(t.get("pnl", 0) or 0) < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def turnover(equity: pd.Series, trade_notional: float) -> float:
    """换手率（累计成交名义额 / 平均净值）。"""
    if equity.empty:
        return 0.0
    avg_equity = float(equity.mean())
    return float(trade_notional / avg_equity) if avg_equity > 0 else 0.0


def compute_metrics(
    equity: pd.Series,
    interval: str,
    trades: list[dict[str, Any]] | None = None,
    trade_notional: float = 0.0,
    benchmark: pd.Series | None = None,
    initial_equity: float | None = None,
) -> dict[str, Any]:
    """汇总指标卡所需全部指标。"""
    trades = trades or []
    rets = equity_to_returns(equity)
    if initial_equity is not None and len(equity):
        first_ret = float(equity.iloc[0] / initial_equity - 1.0)
        rets = pd.concat([pd.Series([first_ret]), rets], ignore_index=True)
        running_peak = equity.cummax().clip(lower=float(initial_equity))
        mdd = float((equity / running_peak - 1.0).min())
        total = float(equity.iloc[-1] / initial_equity - 1.0)
        periods = len(equity)
        ratio = float(equity.iloc[-1] / initial_equity)
        ann = float(ratio ** (_annual_factor(interval) / max(periods, 1)) - 1) if ratio > 0 else -1.0
    else:
        mdd = max_drawdown(equity)
        total = total_return(equity)
        ann = annual_return(equity, interval)

    m: dict[str, Any] = {
        "total_return": round(total, 6),
        "annual_return": round(ann, 6),
        "volatility": round(volatility(rets, interval), 6),
        "sharpe": round(sharpe_ratio(rets, interval), 3),
        "sortino": round(sortino_ratio(rets, interval), 3),
        "calmar": round(ann / abs(mdd), 3) if mdd < 0 else 0.0,
        "max_drawdown": round(mdd, 6),
        "win_rate": round(win_rate(trades), 4),
        "profit_factor": round(profit_factor(trades), 3) if trades else 0.0,
        "num_trades": len(trades),
        "turnover": round(turnover(equity, trade_notional), 4),
        "benchmark_return": 0.0,
        "alpha": 0.0,
        "beta": 0.0,
    }

    # 基准对比（如有）
    if benchmark is not None and len(benchmark) == len(equity):
        bench_rets = equity_to_returns(benchmark)
        m["benchmark_return"] = round(total_return(benchmark), 6)
        if len(rets) == len(bench_rets) and rets.std(ddof=1) > 0:
            # 用覆盖对齐
            joined = pd.concat([rets, bench_rets], axis=1).dropna()
            if len(joined) > 1 and joined.iloc[:, 1].std(ddof=1) > 0:
                beta = float(np.cov(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1] / np.var(joined.iloc[:, 1]))
                m["beta"] = round(beta, 3)
                r_rf = 0.0
                m["alpha"] = round(float(joined.iloc[:, 0].mean() * _annual_factor(interval) - r_rf - beta * (joined.iloc[:, 1].mean() * _annual_factor(interval) - r_rf)), 4)

    return json_safe(m)
