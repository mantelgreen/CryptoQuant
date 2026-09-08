"""统一因子计算：内置 registry 因子 或 用户/AI 表达式因子。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .registry import compute_factor, get_factors
from .sandbox import safe_eval_expr


def meta_or_extra(code: str, extras: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    meta = get_factors().get(code)
    if meta is not None:
        return meta
    if extras and code in extras:
        return extras[code]
    return None


def compute_series(
    code: str, df: pd.DataFrame, extras: dict[str, dict[str, Any]] | None = None
) -> pd.Series:
    """计算因子序列：内置走注册函数；用户表达式走 AST 白名单求值。"""
    meta = get_factors().get(code)
    if meta is not None:
        return compute_factor(code, df)
    if extras and code in extras:
        expr = (extras[code].get("expression") or "").strip()
        if not expr:
            raise KeyError(f"empty_expression:{code}")
        return safe_eval_expr(expr, df).astype(float)
    raise KeyError(f"unknown_factor:{code}")
