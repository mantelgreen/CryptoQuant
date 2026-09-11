"""训练切分回归测试：流水线默认不切研究窗内测试集（test_ratio=0）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.factor.ml import run_ml_research


class _FakeRepo:
    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df

    async def query_or_aggregate(self, symbol, interval, start, end, limit=None):
        d = self.df[(self.df["open_time"] >= start) & (self.df["open_time"] <= end)]
        return d.to_dict("records")


def _df(n: int = 800, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.004, n)
    close = 100 * np.exp(np.cumsum(ret))
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    ts = [t0 + timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": ts,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": rng.uniform(1e6, 2e6, n),
        }
    )


_EXTRAS = {
    "f1": {"expression": "close.pct_change(10)", "name": "mom10", "min_bars": 0},
    "f2": {"expression": "close.pct_change(30)", "name": "mom30", "min_bars": 0},
    "f3": {"expression": "close.rolling(20).mean()/close - 1", "name": "rev20", "min_bars": 0},
}


async def _train(df, **over):
    repo = _FakeRepo(df)
    args = dict(
        horizon=1,
        test_ratio=0.0,
        val_ratio=0.15,
        model="ridge",
        max_corr=0.8,
        extras=_EXTRAS,
    )
    args.update(over)
    return await run_ml_research(
        repo,
        ["f1", "f2", "f3"],
        ["BTCUSDT"],
        "1d",
        df["open_time"].iloc[0],
        df["open_time"].iloc[-1],
        **args,
    )


async def test_no_internal_test():
    r = await _train(_df())
    assert r["found"] is True
    assert r["n_test"] == 0
    assert r["oos_ic"] is None
    assert r["n_val"] > 0
    assert (r.get("sample_test") or []) == []


async def test_train_share_is_majority():
    r = await _train(_df())
    total = r["n_train"] + r["n_val"]
    assert r["n_train"] / total > 0.7  # 默认 val_ratio=0.15 → 训练集约 85%


async def test_no_test_has_more_train_than_with_test():
    df = _df()
    r_no = await _train(df, test_ratio=0.0)
    r_test = await _train(df, test_ratio=0.25)
    assert r_no["n_train"] > r_test["n_train"]
    assert r_no["n_test"] == 0 and r_test["n_test"] > 0


async def test_internal_test_when_requested():
    r = await _train(_df(), test_ratio=0.25)
    assert r["found"] is True
    assert r["n_test"] > 0
    assert r["n_train"] > 0 and r["n_val"] > 0
