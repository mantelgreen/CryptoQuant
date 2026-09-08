"""向量化回测引擎（M2 v1）。

流水线：Signal(模板) → Position → Fill(次根开盘撮合,含手续费/滑点) → Equity → Metrics。
对任意策略模板，产出净值序列、成交列表、指标与回撤序列。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .metrics import compute_metrics, drawdown_series
from .templates import run_template


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    fee_rate: float = 0.001        # 单边 taker 0.1%（现货默认）
    slippage_bps: float = 5.0      # 滑点 0.05%
    allow_short: bool = False


def _apply_fees_slippage(position: pd.Series, price: pd.Series, cfg: BacktestConfig) -> pd.Series:
    """目标仓位到实际执行：次根价格 + 手续费 + 滑点。

    返回已扣除摩擦的收益率序列（按 bar）。
    """
    pos = position.reindex(price.index).fillna(0.0)
    if cfg.allow_short:
        pos = pos.clip(-1, 1)
    else:
        pos = pos.clip(0, 1)

    # 目标仓位 diff = 换手（每根 bar 需要买卖的比例）
    trade = pos.diff().fillna(pos.iloc[0])
    cost_per_trade = cfg.fee_rate + cfg.slippage_bps / 10_000
    # 收益：仓位 * 价格变动率 - 换手成本
    ret = pos.shift(1).fillna(0.0) * price.pct_change().fillna(0.0) - trade.abs() * cost_per_trade
    # 首根按初始仓位开仓，同样计成本
    ret.iloc[0] = -abs(pos.iloc[0]) * cost_per_trade
    return ret


def run_backtest_vectorized(
    ohlcv: pd.DataFrame,
    template_id: str,
    params: dict[str, Any] | None = None,
    cfg: BacktestConfig | None = None,
    interval: str = "1d",
) -> dict[str, Any]:
    """执行一次向量化回测。

    ohlcv 需含列: open high low close volume, index 为时间。
    返回 dict：{equity: Series, returns, drawdown, metrics, trades, position}
    """
    params = params or {}
    cfg = cfg or BacktestConfig()
    df = ohlcv.dropna(subset=["open", "high", "low", "close"]).copy()

    # 目标仓位信号
    target = run_template(template_id, df, params)

    price = df["close"]
    pos = target.reindex(price.index).fillna(0.0)
    if cfg.allow_short:
        pos = pos.clip(-1, 1)
    else:
        pos = pos.clip(0, 1)

    # 收益序列（含摩擦）
    rets = _apply_fees_slippage(pos, price, cfg)

    equity = cfg.initial_cash * (1 + rets).cumprod()
    equity.index = pd.to_datetime(df.index)

    # 从仓位 diff 提取成交记录
    trades = _extract_trades(pos, price, df.index, cfg, params.get("symbol", ""))

    metrics = compute_metrics(
        equity=equity,
        interval=interval,
        trades=trades,
        trade_notional=float((trades[0].get("notional", 0) if trades else 0) * 2) if trades else 0.0,
    )

    dd = drawdown_series(equity)

    return {
        "equity": equity,
        "returns": equity.pct_change().fillna(0.0),
        "drawdown": dd,
        "metrics": metrics,
        "trades": trades,
        "position": pos,
    }


def _extract_trades(
    pos: pd.Series,
    price: pd.Series,
    idx: pd.DatetimeIndex,
    cfg: BacktestConfig,
    symbol: str,
) -> list[dict[str, Any]]:
    """从仓位序列变化推导成交（简化：调仓视为一次成交）。"""
    diff = pos.diff().fillna(pos.iloc[0])
    trades: list[dict[str, Any]] = []
    for i in range(len(diff)):
        d = float(diff.iloc[i])
        if abs(d) < 1e-12:
            continue
        side = "buy" if d > 0 else "sell"
        # 成交价 = 当前 bar 开盘价（次根撮合的近似：用 close 近似演示）
        px = float(price.iloc[i])
        qty = abs(d)  # 名义仓位比例变化
        notional = px * qty
        fee = notional * cfg.fee_rate
        trades.append({
            "ts": str(pd.to_datetime(idx[i])),
            "symbol": symbol,
            "side": side,
            "price": round(px, 8),
            "qty": round(qty, 8),
            "fee": round(fee, 6),
            "notional": round(notional, 6),
            "pnl": 0.0,
        })
    return trades
