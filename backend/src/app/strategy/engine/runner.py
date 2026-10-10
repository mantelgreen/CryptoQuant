"""向量化回测引擎（M2 v1）。

流水线：Signal(模板) → Position → Fill(次根开盘撮合,含手续费/滑点) → Equity → Metrics。
对任意策略模板，产出净值序列、成交列表、指标与回撤序列。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from .metrics import compute_metrics
from .templates import run_template


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    fee_rate: float = 0.001        # 单边 taker 0.1%（现货默认）
    slippage_bps: float = 5.0      # 滑点 0.05%
    allow_short: bool = False


def _apply_fees_slippage(
    position: pd.Series,
    open_price: pd.Series,
    close_price: pd.Series,
    cfg: BacktestConfig,
) -> pd.Series:
    """开盘执行目标仓位，持有至下一根开盘（最后一根持有至收盘）。"""
    pos = position.reindex(open_price.index).fillna(0.0)
    if cfg.allow_short:
        pos = pos.clip(-1, 1)
    else:
        pos = pos.clip(0, 1)

    # 目标仓位 diff = 换手（每根 bar 需要买卖的比例）
    trade = pos.diff().fillna(pos.iloc[0])
    cost_per_trade = cfg.fee_rate + cfg.slippage_bps / 10_000
    # 仓位在本根 open 成交，赚取本根 open→下根 open 收益；末根以 close 结算。
    next_open_return = open_price.shift(-1).div(open_price).sub(1.0)
    if len(next_open_return):
        next_open_return.iloc[-1] = close_price.iloc[-1] / open_price.iloc[-1] - 1.0
    ret = pos * next_open_return.fillna(0.0) - trade.abs() * cost_per_trade
    # 期末按最后收盘价平仓，避免未计退出手续费而高估净收益。
    if len(ret):
        ret.iloc[-1] -= abs(float(pos.iloc[-1])) * cost_per_trade
    return ret


def run_backtest_vectorized(
    ohlcv: pd.DataFrame,
    template_id: str,
    params: dict[str, Any] | None = None,
    cfg: BacktestConfig | None = None,
    interval: str = "1d",
    evaluation_start: datetime | pd.Timestamp | None = None,
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

    # 模板用完整 K 线算信号，信号在收盘后可用；下一根 open 才能成交。
    full_index = pd.DatetimeIndex(df.index)
    signal = target.reindex(df.index).fillna(0.0)
    fill_signal = signal.shift(1)
    pos = fill_signal.fillna(0.0)
    if cfg.allow_short:
        pos = pos.clip(-1, 1)
    else:
        pos = pos.clip(0, 1)

    if evaluation_start is not None:
        cutoff = pd.Timestamp(evaluation_start)
        if full_index.tz is None and cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        elif full_index.tz is not None and cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(full_index.tz)
        elif full_index.tz is not None and cutoff.tzinfo is not None:
            cutoff = cutoff.tz_convert(full_index.tz)
        mask = full_index >= cutoff
        if not mask.any():
            raise ValueError("evaluation_start_after_data")
        df = df.loc[mask]
        signal = signal.loc[df.index]
        fill_signal = fill_signal.loc[df.index]
        pos = pos.loc[df.index]

    price = df["open"]
    close = df["close"]

    # 收益序列（含摩擦）
    rets = _apply_fees_slippage(pos, price, close, cfg)

    equity = cfg.initial_cash * (1 + rets).cumprod()
    equity.index = pd.to_datetime(df.index)

    # 从仓位 diff 提取成交记录（含触发信号值与摊薄成本估算的已实现 PnL）
    trades = _extract_trades(
        pos, fill_signal, price, close, df.index, cfg, params.get("symbol", "")
    )

    turnover_fraction = pos.diff().abs().fillna(abs(float(pos.iloc[0])) if len(pos) else 0.0)
    if len(turnover_fraction):
        turnover_fraction.iloc[-1] += abs(float(pos.iloc[-1]))
    capital_before_trade = equity.shift(1).fillna(cfg.initial_cash)
    traded_notional = float((turnover_fraction * capital_before_trade).sum())
    metrics = compute_metrics(
        equity=equity,
        interval=interval,
        trades=trades,
        trade_notional=traded_notional,
        initial_equity=cfg.initial_cash,
    )

    dd = equity / equity.cummax().clip(lower=cfg.initial_cash) - 1.0
    bar_returns = equity.pct_change().fillna(0.0)
    if len(bar_returns):
        bar_returns.iloc[0] = equity.iloc[0] / cfg.initial_cash - 1.0

    return {
        "equity": equity,
        "returns": bar_returns,
        "drawdown": dd,
        "metrics": metrics,
        "trades": trades,
        "position": pos,
    }


def _leg_reason(prev_pos: float, next_pos: float) -> str:
    """开/加/减/平/反手 的中文操作原因。"""
    eps = 1e-12
    if abs(prev_pos) < eps:
        return "开多" if next_pos > 0 else "开空" if next_pos < 0 else "平"
    if abs(next_pos) < eps:
        return "平多" if prev_pos > 0 else "平空"
    if prev_pos * next_pos < 0:
        return "反手"
    if abs(next_pos) > abs(prev_pos):
        return "加仓"
    return "减仓"


def _extract_trades(
    pos: pd.Series,
    target: pd.Series,
    price: pd.Series,
    close_price: pd.Series,
    idx: pd.DatetimeIndex,
    cfg: BacktestConfig,
    symbol: str,
) -> list[dict[str, Any]]:
    """从仓位序列变化推导成交（调仓视为一次成交）。

    每笔记录：方向/价格/数量/手续费/名义额、触发该笔的信号值(target)、
    操作原因（开/加/减/平/反手）以及用“摊薄成本”估算的已实现 PnL（未扣费）。
    开仓方向累计成本价；平仓/减仓时按 (现价−成本)×数量（做空相反）实现盈亏。
    """
    eps = 1e-12
    trades: list[dict[str, Any]] = []
    inv = 0.0          # 当前持仓量的绝对值
    avg_cost = 0.0     # 摊薄成本价（正方向口径）
    direction = 0      # +1 多 / -1 空

    def _signal(i: int) -> float:
        if target is None:
            return float(pos.iloc[i])
        v = target.iloc[i]
        try:
            f = float(v)
            return f if f == f else float(pos.iloc[i])  # NaN → 用仓位兜底
        except (TypeError, ValueError):
            return float(pos.iloc[i])

    def _close_realized(px: float, qty: float) -> float:
        if direction > 0:
            return (px - avg_cost) * qty
        return (avg_cost - px) * qty

    for i in range(len(pos)):
        prev_pos = float(pos.iloc[i - 1]) if i > 0 else 0.0
        next_pos = float(pos.iloc[i])
        d = next_pos - prev_pos
        if abs(d) < eps:
            continue

        px = float(price.iloc[i])
        qty = abs(d)
        fee = px * qty * cfg.fee_rate
        realized = 0.0
        basis0 = avg_cost  # 本次成交前的持仓成本（减/平时用于记录“这笔的买入均价”）

        # —— 更新持仓/成本（摊薄成本法）——
        same_side = direction == 0 or next_pos * direction > 0
        if inv < eps:
            # 空仓新开
            direction = 1 if next_pos > 0 else -1
            inv = abs(next_pos)
            avg_cost = px
        elif same_side and abs(next_pos) > inv:
            # 同向加仓：按摊薄成本加权
            add = abs(next_pos) - inv
            avg_cost = (inv * avg_cost + add * px) / abs(next_pos)
            inv = abs(next_pos)
        elif same_side or abs(next_pos) < eps:
            # 同向减仓 / 减到 0
            realized = _close_realized(px, inv - abs(next_pos))
            inv = abs(next_pos)
            if inv < eps:
                avg_cost = 0.0
        else:
            # 反向穿零（反手）：先平旧仓，再按新方向开仓
            realized = _close_realized(px, inv)
            direction = 1 if next_pos > 0 else -1
            inv = abs(next_pos)
            avg_cost = px

        # 买卖均价：加/开仓时=摊薄后的买入均价；减/平时=被平掉的这笔的原持仓成本
        basis = avg_cost if (d > 0 and inv >= eps) else (basis0 if basis0 else avg_cost)

        side = "buy" if d > 0 else "sell"
        trades.append({
            "ts": str(pd.to_datetime(idx[i])),
            "symbol": symbol,
            "side": side,
            "price": round(px, 8),
            "qty": round(qty, 8),
            "fee": round(fee, 6),
            "notional": round(px * qty, 6),
            "pnl": round(realized, 6),
            "signal": round(_signal(i), 6),
            "position": round(next_pos, 6),
            "basis": round(basis, 8),
            "reason": _leg_reason(prev_pos, next_pos),
        })

    # 回测区间末按最后收盘价强制平仓，与收益序列中的期末退出费用一致。
    if len(pos) and abs(float(pos.iloc[-1])) >= eps:
        px = float(close_price.iloc[-1])
        qty = abs(float(pos.iloc[-1]))
        fee = px * qty * cfg.fee_rate
        realized = _close_realized(px, qty)
        trades.append({
            "ts": str(pd.to_datetime(idx[-1])),
            "symbol": symbol,
            "side": "sell" if pos.iloc[-1] > 0 else "buy",
            "price": round(px, 8),
            "qty": round(qty, 8),
            "fee": round(fee, 6),
            "notional": round(px * qty, 6),
            "pnl": round(realized, 6),
            "signal": round(_signal(len(pos) - 1), 6),
            "position": 0.0,
            "basis": round(avg_cost, 8),
            "reason": "期末平仓",
        })
    return trades
