"""训练学习曲线数值舍入回归测试：小量级 MSE 不应被抹成 0。"""
from __future__ import annotations

import math

from app.factor.ml import _round_sig


def test_small_mse_not_collapsed():
    # 1 分钟级别收益的 MSE ~1e-10，定点舍入(round 8)会变 0
    v = 9.607557567951368e-11
    r = _round_sig(v)
    assert r != 0.0
    assert math.isclose(r, v, rel_tol=1e-5)


def test_small_values_stay_distinct():
    a, b = 9.6075e-11, 8.1234e-11
    assert _round_sig(a) != _round_sig(b)


def test_normal_scale_preserved():
    v = 0.00054485
    assert math.isclose(_round_sig(v), v, rel_tol=1e-5)


def test_zero_and_nan():
    assert _round_sig(0.0) == 0.0
    assert math.isnan(_round_sig(float("nan")))
