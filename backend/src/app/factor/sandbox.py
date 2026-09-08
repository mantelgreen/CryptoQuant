"""因子草稿沙箱：AST 白名单 + 受限多语句 DSL。

- 支持逐条赋值：`ma = close.rolling(5).mean()`，末行返回可校验的表达式。
- 只允许 K 线列/已赋值变量 + 受限方法（shift/rolling/mean/...），无 import/副作用。
- 自动把 `5%` 写成 `/100`，对 `max(seq)` 等给“该用什么写法”的提示。
"""
from __future__ import annotations

import ast
import re
from typing import Any

import pandas as pd

COLUMNS = {"open", "high", "low", "close", "volume", "quote_volume"}
CALLABLE_ATTRS = {
    "shift", "diff", "rolling", "mean", "std", "pct_change", "abs", "clip",
    "rank", "sum", "cumsum", "cummax", "cummin", "cumprod", "fillna", "skew",
    "ewm", "sign", "log", "min", "max", "ffill", "bfill", "var", "median",
    "quantile", "round", "pow",
}
ALLOWED_KW = {"window", "min_periods", "periods", "span", "alpha", "center", "com", "q", "lower", "upper", "ddof"}
BOOLOPS = {ast.And, ast.Or}
SPECIAL_NAME_HINT = {
    "max": "请用 high.rolling(n).max() 链式写法（不支持 max(序列)）",
    "min": "请用 low.rolling(n).min() 链式写法",
    "sum": "请用 volume.rolling(n).sum() 链式写法",
    "abs": "请用 abs() 或 .abs()（元素级）",
}


class SandboxError(ValueError):
    pass


def _normalize_pct(expr: str) -> str:
    return re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1 / 100.0)", expr)


class _Checker(ast.NodeVisitor):
    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def visit(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Name):
            if node.id not in self._allowed:
                hint = SPECIAL_NAME_HINT.get(node.id)
                extra = f"。{hint}" if hint else ""
                raise SandboxError(f"只允许使用 K 线列或已赋值变量，出现 {node.id!r}{extra}")
            return None
        if isinstance(node, (ast.Constant,)):
            if isinstance(node.value, (int, float, bool)):
                return None
            raise SandboxError("仅允许数值/布尔常量")
        if isinstance(node, ast.BinOp):
            self.visit(node.left)
            self.visit(node.right)
            return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert, ast.Not)):
            self.visit(node.operand)
            return None
        if isinstance(node, ast.Attribute):
            if node.attr not in CALLABLE_ATTRS:
                raise SandboxError(f"方法 {node.attr!r} 不在白名单，支持 shift/rolling/mean/std/abs/... 等")
            self.visit(node.value)
            return None
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "abs" and len(node.args) == 1 and not node.keywords:
                self.visit(node.args[0])
                return None
            if isinstance(func, ast.Name):
                hint = SPECIAL_NAME_HINT.get(func.id)
                if hint:
                    raise SandboxError(f"不支持的函数 {func.id}（{hint}）")
                raise SandboxError(f"不支持直接调用 {func.id}()，请用 K 线列+白名单方法的链式写法")
            if not isinstance(func, ast.Attribute) or func.attr not in CALLABLE_ATTRS:
                raise SandboxError(
                    f"方法 {func.attr if isinstance(func, ast.Attribute) else '?'} 不在白名单，"
                    "支持：shift/rolling/mean/std/abs/...（请用 K 线列+白名单方法的链式写法）"
                )
            for a in node.args:
                if not isinstance(a, ast.Constant) or not isinstance(a.value, (int, float)):
                    raise SandboxError("方法位置参数须为数值常量")
            for kw in node.keywords:
                if kw.arg not in ALLOWED_KW:
                    raise SandboxError(f"方法不支持关键字 {kw.arg}，可用：{sorted(ALLOWED_KW)}")
                if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, (int, float, bool)):
                    raise SandboxError(f"关键字 {kw.arg} 的值须为常量")
            self.visit(func.value)
            return None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, tuple(BOOLOPS)):
            for v in node.values:
                self.visit(v)
            return None
        if isinstance(node, ast.Compare):
            if not all(isinstance(o, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.ops):
                raise SandboxError("不支持该比较运算")
            self.visit(node.left)
            for c in node.comparators:
                self.visit(c)
            return None
        raise SandboxError(f"不支持该表达式结构：{type(node).__name__}")


def _eval_single(expr: str, env: dict[str, Any], allowed: set[str]) -> pd.Series:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SandboxError(
            f"表达式语法错误：{exc.msg}。请勿使用 5% 写法（改为 0.05），"
            "并使用 K 线列/已赋值变量的链式方法（如 high.rolling(10).max()）。"
        ) from exc
    _Checker(allowed).visit(tree)
    try:
        code = compile(tree, "<factor>", "eval")
        result = eval(code, {"__builtins__": {}}, env)  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        raise SandboxError(f"求值失败：{exc}") from exc
    if not isinstance(result, pd.Series):
        raise SandboxError("表达式结果不是序列（请返回整列运算结果）")
    out = pd.to_numeric(result, errors="coerce").astype(float)
    return out.replace([float("inf"), float("-inf")], float("nan"))


def safe_eval_expr(expr: str, df: pd.DataFrame) -> pd.Series:
    """校验并求值表达式/受限多语句 DSL，返回与 df.index 对齐的 pd.Series。"""
    expr = _normalize_pct(expr)
    env: dict[str, Any] = {"abs": abs}
    allowed: set[str] = set(COLUMNS)
    for col in COLUMNS:
        if col in df.columns:
            env[col] = df[col]

    statements = [s for s in re.split(r"[;\n]", expr) if s.strip()]
    last_series: pd.Series | None = None
    for raw in statements:
        st = raw.strip()
        if not st:
            continue
        if "=" in st:
            name, _, val = st.partition("=")
            name = name.strip()
            if not name.isidentifier():
                raise SandboxError(f"变量名非法：{name!r}")
            if name in COLUMNS:
                raise SandboxError(f"不能用 {name} 作为变量名（保留列名）")
            series_val = _eval_single(val.strip(), env, allowed)
            env[name] = series_val
            allowed.add(name)
            last_series = series_val
        else:
            last_series = _eval_single(st, env, allowed)
    if last_series is None:
        raise SandboxError("没有可校验的表达式")
    return last_series
