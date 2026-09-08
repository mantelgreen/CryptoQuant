"""因子注册表 v0：内置因子 = Python 函数，输入 K 线 DataFrame，输出因子序列（与 df.index 对齐）。

df 约定列：open / high / low / close / volume；index 为时间。
后续可由 @register_factor 装饰器自动扫描扩展（M3 配合 AI 沙箱入库）。
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

FactorFn = Callable[[pd.DataFrame], pd.Series]


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def f_momentum_12_1(df: pd.DataFrame) -> pd.Series:
    return (df["close"].shift(12) / df["close"].shift(1) - 1).fillna(0.0)


def f_rsi_oversold_rev(df: pd.DataFrame) -> pd.Series:
    return (-(_rsi(df["close"], 14) - 50) / 50).fillna(0.0)


def f_vol_squeeze(df: pd.DataFrame) -> pd.Series:
    atr = _atr(df, 14)
    std = df["close"].rolling(60).std()
    return (-atr / std.replace(0, np.nan)).fillna(0.0)


def f_obv_slope(df: pd.DataFrame) -> pd.Series:
    obv = _obv(df)
    return (obv.rolling(10).mean() - obv.rolling(3).mean()).fillna(0.0)


def f_day_range_pos(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return ((df["close"] - df["low"]) / rng - 0.5).fillna(0.0)


def f_volume_ratio(df: pd.DataFrame) -> pd.Series:
    return (df["volume"] / df["volume"].rolling(20).mean() - 1).fillna(0.0)


def register_factors() -> dict[str, dict[str, Any]]:
    return {
        "momentum_12_1": {
            "code": "momentum_12_1",
            "name": "12-1 动量",
            "description": "过去 12 期收益减最近 1 期收益，捕捉中短期趋势延续。",
            "tags": ["动量"],
            "min_bars": 20,
            "fn": f_momentum_12_1,
        },
        "rsi_oversold_rev": {
            "code": "rsi_oversold_rev",
            "name": "RSI 超卖反转",
            "description": "RSI 深度超卖后的均值回归倾向（负值越大越超卖）。",
            "tags": ["反转", "均值回归"],
            "min_bars": 40,
            "fn": f_rsi_oversold_rev,
        },
        "vol_squeeze": {
            "code": "vol_squeeze",
            "name": "波动率收缩",
            "description": "ATR 相对波动缩放后的收缩程度，常领先方向性突破。",
            "tags": ["波动"],
            "min_bars": 80,
            "fn": f_vol_squeeze,
        },
        "obv_slope": {
            "code": "obv_slope",
            "name": "OBV 资金斜率",
            "description": "量能方向对价格的验证：OBV 短长均线差。",
            "tags": ["资金流", "量价"],
            "min_bars": 40,
            "fn": f_obv_slope,
        },
        "day_range_pos": {
            "code": "day_range_pos",
            "name": "日内区间位置",
            "description": "收盘价在当日高低区间的相对位置，刻画买卖压力。",
            "tags": ["量价", "反转"],
            "min_bars": 30,
            "fn": f_day_range_pos,
        },
        "volume_ratio": {
            "code": "volume_ratio",
            "name": "量比异动",
            "description": "成交量相对 20 期均值的放量程度。",
            "tags": ["量价", "动量"],
            "min_bars": 30,
            "fn": f_volume_ratio,
        },
    }


_FACTORS: dict[str, dict[str, Any]] | None = None


def get_factors() -> dict[str, dict[str, Any]]:
    global _FACTORS
    if _FACTORS is None:
        _FACTORS = register_factors()
    return _FACTORS


def get_factor(code: str) -> dict[str, Any] | None:
    return get_factors().get(code)


def list_factor_meta(exclude_fn: bool = True) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for code, meta in get_factors().items():
        item = {k: v for k, v in meta.items() if not exclude_fn or k != "fn"}
        items.append(item)
    return items


def compute_factor(code: str, df: pd.DataFrame) -> pd.Series:
    meta = get_factor(code)
    if meta is None:
        raise KeyError(f"unknown_factor:{code}")
    return meta["fn"](df).astype(float)
