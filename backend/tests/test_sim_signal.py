"""模拟盘自动决策规则回归测试（与回测 ml_score 对齐）。"""
from __future__ import annotations

import pandas as pd

from app.simtrader.service import _decide_signal


def test_quantile_rule_enter_on_new_high():
    scores = pd.Series([float(i) for i in range(100)])
    enter, exit_, rule = _decide_signal(scores, {})
    assert rule["type"] == "quantile"
    assert enter is True
    assert exit_ is False


def test_quantile_rule_exit_on_new_low():
    scores = pd.Series([float(100 - i) for i in range(100)])
    enter, exit_, rule = _decide_signal(scores, {})
    assert rule["type"] == "quantile"
    assert exit_ is True
    assert enter is False


def test_quantile_rule_flat_series_holds():
    scores = pd.Series([0.0] * 100)
    enter, exit_, rule = _decide_signal(scores, {})
    assert rule["type"] == "quantile"
    assert enter is False
    assert exit_ is False


def test_legacy_absolute_thresholds():
    scores = pd.Series([0.05] * 100)
    enter, exit_, rule = _decide_signal(scores, {"enter": 0.02, "exit": -0.02})
    assert rule["type"] == "absolute"
    assert enter is True
    assert exit_ is False


def test_quantile_takes_precedence_over_legacy_keys():
    scores = pd.Series([float(i) for i in range(100)])
    _enter, _exit, rule = _decide_signal(
        scores, {"enter": 0.02, "exit": -0.02, "enter_q": 0.8, "exit_q": 0.2}
    )
    assert rule["type"] == "quantile"
