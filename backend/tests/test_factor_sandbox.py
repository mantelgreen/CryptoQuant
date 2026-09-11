"""因子沙箱回归测试：覆盖 LLM 常生成的表达式形态与白名单边界。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factor.sandbox import SandboxError, safe_eval_expr, validate_expression


@pytest.fixture()
def df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 400
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.004, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.004, n))),
            "close": close,
            "volume": rng.uniform(1e6, 2e6, n),
            "quote_volume": rng.uniform(1e8, 2e8, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def _ok(expr: str, df: pd.DataFrame) -> pd.Series:
    out = safe_eval_expr(expr, df)
    assert isinstance(out, pd.Series)
    assert len(out) == len(df)
    return out


def test_basic_ratio(df):
    _ok("close.rolling(5).mean() / close - 1", df)


@pytest.mark.parametrize(
    "expr",
    [
        "close >= high.rolling(20).max()",
        "close == open",
        "close != open",
        "close <= low.rolling(20).min()",
        "close > open",
    ],
)
def test_comparisons(df, expr):
    _ok(expr, df)


@pytest.mark.parametrize(
    "expr",
    [
        "close > open and volume > 1e6",
        "close > open or volume < 1e6",
        "(close > open) & (volume > 1e6)",
        "low < close < high",
    ],
)
def test_logical_and_chained(df, expr):
    _ok(expr, df)


@pytest.mark.parametrize(
    "expr",
    [
        # LLM 常漏写第一组比较的括号，且习惯加 `& 1` 把布尔转 0/1（系统会自动转，可省）
        "close > sma_20 & (close > close.shift(12)) & volume > volume.shift(1) & 1",
        "close > boll_lower & close < boll_upper",
        "close > sma_20 | rsi < 30",
        "flag = close > open\nflag & close > close.shift(1) & True",
    ],
)
def test_loose_bitwise_conjunction(df, expr):
    _ok(expr, df)


def test_repair_conjunction_drops_cast(df):
    from app.factor.sandbox import _repair_conjunctions

    assert _repair_conjunctions("close > open & volume > 1e6 & 1") == "(close > open)&(volume > 1e6)"


@pytest.mark.parametrize(
    "expr",
    [
        # LLM 把 and/or 写成函数调用（Python 关键字不能当函数）
        "and(close > open, volume > 1e6)",
        "where(close > open, or(rsi < 30, close > sma_20), 0)",
        # 给内置指标序列加括号（ema_20(close)）
        "ema_20(close) > ema_20.shift(1)",
        "where(ema_20(close) > ema_5(close), 1, 0)",
        # 内置指标带数值参数（boll_mid(20) / macd(12, 26, 9)）
        "macd(12, 26, 9) > 0",
        "close > boll_mid(20) & close < boll_upper(20)",
        "close > (close / boll_mid(20)) * boll_upper(20) & close < (close / boll_mid(20)) * boll_lower(20)",
        # “列名_周期”臆造变量 → 展开成该列滚动均值
        "(close > sma_20) and (volume > volume_20)",
        "close > close_10",
    ],
)
def test_llm_functional_syntax_repairs(df, expr):
    _ok(expr, df)


def test_column_period_assigned_var_preserved(df):
    # 用户自己赋过名的 `volume_20` 不应被再次展开
    expr = "volume_20 = volume.rolling(20).mean()\nclose > volume_20"
    _ok(expr, df)


def test_keyword_and_still_works(df):
    # 关键字 and 后跟括号不应被误判为 and(...) 函数调用
    _ok("close > open and (high > low)", df)


def test_indicator_call_on_other_column_rejected(df):
    with pytest.raises(SandboxError):
        safe_eval_expr("sma_20(high) > close", df)


def test_functional_and_is_not_syntax_error_but_arity(df):
    # 用户案例：and(...) 已不再是“invalid syntax”，剩余的是 where 缺第三参数的语义错误
    expr = "where(close > high, and(ema_20(close) > ema_20(close).shift(1), close > high))"
    with pytest.raises(SandboxError) as exc:
        safe_eval_expr(expr, df)
    assert "where()" in str(exc.value)
    assert "invalid syntax" not in str(exc.value)


@pytest.mark.parametrize(
    "expr",
    [
        "close.ewm(span=12).mean()",
        "close.ewm(alpha=0.2).mean()",
        "close.rolling(5, min_periods=1).mean()",
        "close.rolling(5).std(ddof=0)",
        "close.rolling(5).quantile(q=0.5)",
        "close.clip(lower=0, upper=1)",
        "close.shift(periods=1)",
    ],
)
def test_keyword_args(df, expr):
    _ok(expr, df)


@pytest.mark.parametrize(
    "expr",
    [
        "abs(close - open)",
        "log(close)",
        "sign(close - close.shift(1))",
        "where(close > open, 1, 0)",
        "where(close > open, close, open)",
        "where(close > open, where(volume > 1e6, 2, 1), 0)",
    ],
)
def test_element_functions(df, expr):
    _ok(expr, df)


def test_multiline_assignment(df):
    out = safe_eval_expr("ma = close.rolling(5).mean()\nclose / ma - 1", df)
    assert len(out) == len(df)


def test_assignment_with_comparison(df):
    out = safe_eval_expr("flag = close >= open\nflag * volume", df)
    assert len(out) == len(df)


def test_pct_literal(df):
    _ok("close > open * 5%", df)


def test_result_is_finite_or_nan(df):
    out = _ok("close / close.rolling(20).max() * 100", df)
    assert not np.isinf(out.to_numpy()[~np.isnan(out.to_numpy())]).any()


@pytest.mark.parametrize(
    "expr",
    [
        "close.log()",
        "close.sign()",
        "np.where(close > open, 1, 0)",
        "np.log(close)",
        "max(close, open)",
        "min(close, open)",
        "sum(volume)",
        "df['close']",
        "import os",
        "close.tolist()",
        "close.rolling(5, axis=1).mean()",
        "n = 5\nclose.rolling(n).mean()",
        "close > 'x'",
        "close > None",
        "1 if close > open else 0",
        "(lambda x: x)(close)",
        "close(1)",
        "close = close.shift(1)",
        "a b = close",
        "",
        "where(close > open, 1)",
        "where(close > open, 1, 0, x=1)",
        "close.rolling(5).rank(pct=True)",
    ],
)
def test_rejected(df, expr):
    with pytest.raises(SandboxError):
        safe_eval_expr(expr, df)


def test_scalar_result_rejected(df):
    with pytest.raises(SandboxError):
        safe_eval_expr("close.max()", df)


def test_empty_expression(df):
    with pytest.raises(SandboxError):
        safe_eval_expr("   \n  ", df)


def test_return_prefix_accepted(df):
    _ok("return close.pct_change(20)", df)


def test_multiline_return_accepted(df):
    expr = (
        "mom = close.pct_change(20)\n"
        "vol = close.pct_change().rolling(20).std()\n"
        "ratio = mom / (vol + 1e-8)\n"
        "return ratio"
    )
    _ok(expr, df)


def test_inline_comment_stripped(df):
    _ok("ma = close.rolling(5).mean()  # 均线\nclose / ma - 1", df)


def test_validate_expression_ok(df):
    ok, errors, warnings = validate_expression("close.rolling(5).mean() / close - 1")
    assert ok is True
    assert errors == []
    assert warnings == []


def test_validate_expression_reject(df):
    ok, errors, _warnings = validate_expression("close.log()")
    assert ok is False
    assert errors


def test_validate_expression_all_nan_warning(df):
    ok, errors, warnings = validate_expression("close.rolling(1000).mean()")
    assert ok is True
    assert errors == []
    assert any("NaN" in w for w in warnings)


def test_validate_expression_constant_warning(df):
    ok, errors, warnings = validate_expression("close / close")
    assert ok is True
    assert errors == []
    assert any("恒定" in w for w in warnings)


@pytest.mark.parametrize(
    "expr",
    [
        # 全局统计（无窗口）会跨时间使用未来信息 → 必须拒绝
        "close.mean()",
        "close.std()",
        "volume.sum()",
        "close.min()",
        "close.max()",
        "(close - open).mean()",
        "close.quantile(0.5)",
        "close.rank()",
    ],
)
def test_global_reduction_rejected(df, expr):
    with pytest.raises(SandboxError):
        safe_eval_expr(expr, df)


@pytest.mark.parametrize(
    "expr",
    [
        # 限定在滚动/指数窗口上的统计是允许的（因果）
        "close.rolling(20).mean()",
        "close.rolling(20).std()",
        "volume.rolling(20).sum()",
        "high.rolling(20).max()",
        "low.rolling(20).min()",
        "close.ewm(span=12).mean()",
        "close.expanding(60).mean()",
        "close.rolling(20).mean() / close - 1",
    ],
)
def test_windowed_reduction_allowed(df, expr):
    _ok(expr, df)


@pytest.mark.parametrize(
    "expr",
    [
        # 起点依赖的累计运算会导致训练/回测窗口口径不一致 → 拒绝
        "close.cumsum()",
        "close.cummax()",
        "close.cummin()",
        "close.cumprod()",
        # 依赖累计起点的内置指标不再注入
        "obv",
        "vwap",
    ],
)
def test_cumulative_rejected(df, expr):
    with pytest.raises(SandboxError):
        safe_eval_expr(expr, df)
