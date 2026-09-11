"""样本采集回归测试：流水线训练集/测试集样本（前 N 条）。"""
from __future__ import annotations

import pandas as pd

from app.factor.ml import _append_samples


def _sub(n: int = 10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"f1": range(n), "f2": [i * 0.5 for i in range(n)], "label": [i * 0.01 for i in range(n)]},
        index=idx,
    )


def test_append_samples_basic():
    store: list[dict] = []
    sub = _sub()
    _append_samples(store, sub, ["f1", "f2"], "BTCUSDT", 0, 5, 100)
    assert len(store) == 5
    assert store[0]["symbol"] == "BTCUSDT"
    assert "ts" in store[0]
    assert store[0]["f1"] == 0
    assert store[0]["label"] == 0.0


def test_append_samples_respects_limit():
    store: list[dict] = []
    sub = _sub(20)
    _append_samples(store, sub, ["f1"], "ETHUSDT", 0, 20, 3)
    assert len(store) == 3


def test_append_samples_range_and_cross_symbol_accumulate():
    store: list[dict] = []
    sub = _sub()
    _append_samples(store, sub, ["f1"], "BTCUSDT", 5, 10, 100)
    _append_samples(store, sub, ["f1"], "ETHUSDT", 0, 10, 100)
    assert len(store) == 15
    assert store[0]["f1"] == 5  # 从区间 lo 开始
    assert store[-1]["symbol"] == "ETHUSDT"
