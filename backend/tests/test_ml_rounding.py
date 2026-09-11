"""训练学习曲线数值舍入回归测试：小量级 MSE 不应被抹成 0。"""
from __future__ import annotations

import math

import numpy as np

from app.factor.ml import _apply_winsor, _round_sig, _winsor_bounds


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


def test_winsor_skips_sparse_degenerate_column():
    rng = np.random.default_rng(0)
    n = 1200
    sparse = np.zeros(n)
    sparse[rng.choice(n, size=6, replace=False)] = 1.0
    cont = rng.normal(0, 1, n)
    X = np.c_[sparse, cont]
    lo, hi = _winsor_bounds(X, 0.01)
    # 稀疏列退化 → NaN（不裁剪）；连续列正常
    assert math.isnan(lo[0]) and math.isnan(hi[0])
    assert not math.isnan(lo[1])
    out = _apply_winsor(X, lo, hi)
    # 稀疏列不被压成常数
    assert len(np.unique(out[:, 0])) == 2
    # 连续列被钳制到边界内
    assert out[:, 1].min() >= lo[1] - 1e-9
    assert out[:, 1].max() <= hi[1] + 1e-9


def test_winsor_normal_column_still_clipped():
    rng = np.random.default_rng(1)
    X = rng.normal(0, 1, (500, 1))
    X[0, 0] = 100.0
    lo, hi = _winsor_bounds(X, 0.01)
    out = _apply_winsor(X, lo, hi)
    assert out[0, 0] < 100.0

