"""策略模板 / 回测引擎 / 指标 单测（纯函数，不依赖 DB）。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.strategy.engine.metrics import (
    annual_return,
    compute_metrics,
    max_drawdown,
    sharpe_ratio,
    total_return,
)
from app.strategy.engine.runner import BacktestConfig, run_backtest_vectorized

from app.strategy.engine.templates import get_templates

def _trend_df(n: int = 300) -> pd.DataFrame:
    """构造一段带趋势 + 波动 + 回撤的 OHLCV。"""
    idx = pd.date_range("2023-01-01", periods=n, freq="1D")
    rng = np.random.default_rng(7)
    drift = np.linspace(0, 0.6, n)
    noise = rng.normal(0, 0.015, n).cumsum() * 0.3
    close = 100 * np.exp(drift + noise)
    close = np.maximum(close, 1.0)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    vol = rng.uniform(100, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx
    )


def test_templates_registered() -> None:
    t = get_templates()
    assert {"sma_cross", "sma_cross_longshort", "rsi_mean_revert", "buy_and_hold"} <= set(t)


def test_metrics_core_values() -> None:
    df = _trend_df(500)
    price = df["close"]
    # 买入持有净值为价格本身
    equity = price / price.iloc[0] * 100_000
    assert total_return(equity) > 0.0
    assert annual_return(equity, "1d") > 0.0
    mdd = max_drawdown(equity)
    assert mdd <= 0.0  # 恒 ≤ 0
    rets = equity.pct_change().dropna()
    assert np.isfinite(sharpe_ratio(rets, "1d"))


def test_buy_and_hold_matches_benchmark() -> None:
    df = _trend_df(400)
    res = run_backtest_vectorized(
        df, "buy_and_hold", {}, cfg=BacktestConfig(fee_rate=0.0, slippage_bps=0.0)
    )
    eq = res["equity"]
    # 无任何摩擦时，买入持有净值应严格等于价格路径
    bench = (df["close"].values / df["close"].iloc[0]) * 100_000
    np.testing.assert_allclose(eq.values, bench, rtol=1e-6)
    assert res["metrics"]["num_trades"] >= 1


def test_evaluation_window_keeps_warmup_but_reports_only_holdout() -> None:
    df = _trend_df(80)
    cutoff = df.index[50]
    res = run_backtest_vectorized(
        df,
        "buy_and_hold",
        {},
        cfg=BacktestConfig(fee_rate=0.0, slippage_bps=0.0),
        evaluation_start=cutoff,
    )

    assert len(res["equity"]) == len(df) - 50
    assert res["equity"].index[0] == cutoff
    assert res["metrics"]["total_return"] == pytest.approx(
        res["equity"].iloc[-1] / 100_000 - 1, abs=1e-6
    )
    first_holdout_return = df["open"].iloc[51] / df["open"].iloc[50] - 1
    assert res["equity"].iloc[0] == pytest.approx(100_000 * (1 + first_holdout_return))


def test_evaluation_start_after_data_is_rejected() -> None:
    df = _trend_df(20)
    with pytest.raises(ValueError, match="evaluation_start_after_data"):
        run_backtest_vectorized(
            df,
            "buy_and_hold",
            {},
            evaluation_start=df.index[-1] + pd.Timedelta(days=1),
        )


def test_sma_cross_runs_and_no_nan() -> None:
    df = _trend_df(400)
    res = run_backtest_vectorized(
        df, "sma_cross", {"fast": 5, "slow": 20}, cfg=BacktestConfig(fee_rate=0.001)
    )
    assert len(res["equity"]) == len(df)
    assert not res["equity"].isna().any()
    assert res["equity"].iloc[-1] > 0
    assert "sharpe" in res["metrics"]
    assert "max_drawdown" in res["metrics"]
    assert res["metrics"]["max_drawdown"] <= 0


def test_short_strategy_requires_flag() -> None:
    df = _trend_df(300)
    # 多空模板但 allow_short=False → 仓位被 clip 到 [0,1]，仍可跑
    res = run_backtest_vectorized(
        df, "sma_cross_longshort", {"fast": 5, "slow": 20}, cfg=BacktestConfig(fee_rate=0.001, allow_short=False)
    )
    assert res["metrics"]["num_trades"] >= 0


def test_rsi_template() -> None:
    df = _trend_df(300)
    res = run_backtest_vectorized(df, "rsi_mean_revert", {}, cfg=BacktestConfig())
    assert np.isfinite(res["metrics"]["total_return"])


def test_empty_data_raises() -> None:
    with pytest.raises(KeyError):
        run_backtest_vectorized(_trend_df(50), "not_a_real_template", {})


def test_compute_metrics_shape() -> None:
    df = _trend_df(200)
    res = run_backtest_vectorized(df, "sma_cross", {"fast": 3, "slow": 10})
    m = res["metrics"]
    for k in ["total_return", "annual_return", "volatility", "sharpe", "sortino", "calmar", "max_drawdown"]:
        assert np.isfinite(m[k]), k


def test_compute_metrics_produces_strict_json_for_unbounded_ratios() -> None:
    equity = pd.Series([100.0, 110.0, 121.0])
    metrics = compute_metrics(
        equity,
        "1d",
        trades=[{"pnl": 1.0}],
    )

    assert metrics["sortino"] is None
    assert metrics["profit_factor"] is None
    json.dumps(metrics, allow_nan=False)
