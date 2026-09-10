"""截面因子研究：跨标的横截面 IC/IR/t 值与样本不足提示。"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.factor.cross_section import cross_sectional_metrics, research_factor_cs

EXTRAS = {"f1": {"name": "mom5", "expression": "close.pct_change(5)", "min_bars": 0}}


class _FakeRepo:
    def __init__(self, panels: dict[str, list[dict]]):
        self.panels = panels

    async def query_or_aggregate(self, symbol, interval, start, end, limit=None):
        return self.panels.get(symbol, [])


def _make_panel(n: int = 300, alpha: float = 0.15, seed: int = 0) -> dict[str, list[dict]]:
    """带横截面动量的多标的行情：r_t = alpha*mean(past5) + noise。"""
    rng = np.random.default_rng(seed)
    t0 = datetime(2021, 1, 1)
    out: dict[str, list[dict]] = {}
    for si in range(15):
        price = 100.0
        rets: list[float] = []
        rows: list[dict] = []
        for i in range(n):
            past = float(np.mean(rets[-5:])) if len(rets) >= 5 else 0.0
            r = alpha * past + float(rng.normal(0, 0.02))
            rets.append(r)
            price *= 1 + r
            rows.append(
                {
                    "open_time": t0 + timedelta(days=i),
                    "open": price * 0.999,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": float(1000 + rng.integers(0, 500)),
                }
            )
        out[f"SYM{si:02d}USDT"] = rows
    return out


@pytest.mark.asyncio
async def test_cross_sectional_positive_ic():
    repo = _FakeRepo(_make_panel())
    syms = [f"SYM{si:02d}USDT" for si in range(15)]
    r = await research_factor_cs(
        repo,
        "f1",
        syms,
        "1d",
        datetime(2021, 1, 1),
        datetime(2022, 1, 1),
        horizon=1,
        min_names=5,
        neutralize=True,
        extras=EXTRAS,
    )
    assert r["found"], r
    assert r["n_periods"] > 100
    assert r["avg_names"] > 10
    assert r["rank_ic"] is not None and r["rank_ic"] > 0.05
    assert len(r["series"]) > 0


@pytest.mark.asyncio
async def test_cross_sectional_insufficient_names():
    repo = _FakeRepo(_make_panel())
    syms = [f"SYM{si:02d}USDT" for si in range(3)]
    r = await research_factor_cs(
        repo,
        "f1",
        syms,
        "1d",
        datetime(2021, 1, 1),
        datetime(2022, 1, 1),
        horizon=1,
        min_names=5,
        extras=EXTRAS,
    )
    assert r["found"] is False
    assert "标的" in (r.get("message") or "")


def test_cross_sectional_metrics_aggregation():
    # 构造 2 期、每期 6 个标的：因子与未来收益完全同序 → RankIC=1
    import pandas as pd

    F = pd.DataFrame(
        {"a": [1, 2], "b": [2, 3], "c": [3, 4], "d": [4, 5], "e": [5, 6], "f": [6, 7]},
        index=pd.to_datetime(["2021-01-01", "2021-01-02"]),
    )
    R = F * 0.01
    m = cross_sectional_metrics(F, R, min_names=5)
    assert m["found"] is True
    assert m["n_periods"] == 2
    assert m["rank_ic"] == pytest.approx(1.0, abs=1e-6)
    assert m["long_short_spread"] is not None and m["long_short_spread"] > 0
