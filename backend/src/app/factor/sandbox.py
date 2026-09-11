"""因子草稿沙箱：AST 白名单 + 受限多语句 DSL。

- 支持逐条赋值：`ma = close.rolling(5).mean()`，末行返回可校验的表达式。
- 只允许 K 线列/已赋值变量 + 受限方法（shift/rolling/mean/...）与元素级函数（abs/log/sign/where）。
- 支持比较（< > <= >= == !=）、and/or（自动转 &/|）、链式比较（自动转 & 连接）。
- 自动把 `5%` 写成 `/100`，对 `max(seq)` 等给“该用什么写法”的提示。
"""
from __future__ import annotations

import ast
import re
from typing import Any

import numpy as np
import pandas as pd

COLUMNS = {"open", "high", "low", "close", "volume", "quote_volume"}
# 注意：cumsum/cummax/cummin/cumprod 被移除——它们的值依赖序列起点，训练/回测/打分的
# 窗口起点不同会导致同一时刻取值不同（train/serve skew），应改用 rolling(...) 形式。
CALLABLE_ATTRS = {
    "shift", "diff", "rolling", "mean", "std", "pct_change", "abs", "clip",
    "rank", "sum", "fillna", "skew",
    "ewm", "expanding", "min", "max", "ffill", "bfill", "var", "median",
    "quantile", "round", "pow",
}
ALLOWED_KW = {"window", "min_periods", "periods", "span", "alpha", "center", "com", "q", "lower", "upper", "ddof"}
# 统计量（会跨样本聚合）只能作用在滚动/指数窗口上，否则引入未来函数（look-ahead）。
REDUCTION_ATTRS = {"mean", "std", "sum", "min", "max", "median", "var", "skew", "quantile", "rank"}
_WINDOW_RECEIVERS = {"rolling", "ewm", "expanding"}


def _is_windowed_receiver(node: ast.AST) -> bool:
    """判断某个方法的调用对象是否是滚动/指数窗口链（rolling/ewm/expanding）。"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _WINDOW_RECEIVERS
    )


def _asb(x: Any) -> Any:
    """把 0/1（或任意数值）规整为 bool：NaN→False、非 0→True。仅用于 `&`/`|` 连接。"""
    if isinstance(x, pd.Series):
        if x.dtype == bool:
            return x
        return pd.to_numeric(x, errors="coerce").fillna(0.0).ne(0.0)
    if isinstance(x, np.ndarray):
        arr = np.asarray(x)
        if arr.dtype == bool:
            return arr
        return np.nan_to_num(arr).astype(bool)
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x) if x == x else False
    return bool(x)
# 元素级函数（numpy 实现）：name -> (callable, 位置参数个数)
FUNC_WHITELIST: dict[str, tuple[Any, int]] = {
    "abs": (np.abs, 1),
    "log": (np.log, 1),
    "sign": (np.sign, 1),
    "where": (np.where, 3),
    "_asb": (_asb, 1),
}
SPECIAL_NAME_HINT = {
    "max": "请用 high.rolling(n).max() 链式写法（不支持 max(序列)）",
    "min": "请用 low.rolling(n).min() 链式写法",
    "sum": "请用 volume.rolling(n).sum() 链式写法",
}


class SandboxError(ValueError):
    pass


def _normalize_pct(expr: str) -> str:
    return re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1 / 100.0)", expr)


def _is_pure_scalar(text: str) -> bool:
    """是否为纯数值/布尔常量（如 `& 1` 这类“转 0/1”的冗余片段）。"""
    t = text.strip()
    if t in {"True", "False"}:
        return True
    return re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)", t) is not None


def _has_top_level_compare(text: str) -> bool:
    """顶层（括号外）是否出现比较运算符。"""
    depth = 0
    i = 0
    n = len(text)
    two = ("==", "!=", "<=", ">=")
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            if text[i : i + 2] in two:
                return True
            if ch in "<>" and text[i : i + 1] == ch:
                return True
            if ch == "=" and (i == 0 or text[i - 1] not in "=<>!"):
                # 裸 `=` 属赋值，已在上层按语句切分，不会走到这里
                return True
        i += 1
    return False


def _is_fully_wrapped(text: str) -> bool:
    """整串是否已被最外层一对括号包裹（形如 `(...)`）。"""
    t = text.strip()
    if not t.startswith("("):
        return False
    depth = 0
    for i, ch in enumerate(t):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(t) - 1:
                return False
    return depth == 0


def _repair_conjunctions(expr: str) -> str:
    """把“比较/布尔片段用 `&`、`|` 顶层连接”的写法修正为括号完整的形式。

    LLM 常直接写 `close > ma & cond2 & …`，但 `&` 的优先级高于 `>`，
    Python 会解析成 `close > (ma & cond2…)` 导致“float & bool”求值失败。
    这里在顶层把每个含比较的子片段补上括号，并丢弃 `& 1` / `& True` 这类
    把布尔“转 0/1”的冗余常量（布尔结果末尾会自动转 float）。
    """
    if "&" not in expr and "|" not in expr:
        return expr
    parts: list[str] = []
    ops: list[str] = []
    depth = 0
    cur: list[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch in "&|" and depth == 0:
            parts.append("".join(cur))
            ops.append(ch)
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))

    kept: list[str] = []
    kept_idx: list[int] = []
    for idx, p in enumerate(parts):
        if p.strip() and not _is_pure_scalar(p):
            kept.append(p.strip())
            kept_idx.append(idx)
    if not kept:
        return expr
    if len(kept) == 1:
        return kept[0]

    rebuilt: list[str] = []
    for k, text in enumerate(kept):
        if k > 0:
            prev_orig = kept_idx[k - 1]
            cur_orig = kept_idx[k]
            # 两段之间若夹了被丢弃的常量，任取它们之间的一个原始连接符即可
            rebuilt.append(ops[cur_orig - 1])
        if _has_top_level_compare(text) and not _is_fully_wrapped(text):
            rebuilt.append(f"({text})")
        else:
            rebuilt.append(text)
    return "".join(rebuilt)


_LOGIC_CALL_RE = re.compile(r"\b(and|or)\s*\(")


def _repair_logic_calls(expr: str) -> str:
    """把 LLM 常写的函数式逻辑 `and(a, b)` / `or(a, b)` 改写成运算符形式。

    `and`/`or` 是 Python 关键字，`and(a, b)` 是语法错误。这里把它归一化为
    `(a) & (b)` / `(a) | (b)`。为避免误伤合法的关键字写法（如 `x and (y)`），
    仅当 `and(`/`or(` 前面紧跟 `(`/`,`/`=`/`[`/`:`（或串首）时才视为函数调用。
    从最右（最内层）开始处理以支持嵌套。
    """
    for _ in range(200):
        matches = list(_LOGIC_CALL_RE.finditer(expr))
        target: re.Match[str] | None = None
        for m in reversed(matches):
            i = m.start() - 1
            while i >= 0 and expr[i] in " \t\r\n":
                i -= 1
            if i < 0 or expr[i] in "(,=:[":
                target = m
                break
        if target is None:
            return expr
        word = target.group(1)
        op = "&" if word == "and" else "|"
        open_idx = target.end() - 1
        depth = 1
        j = open_idx + 1
        while j < len(expr) and depth:
            if expr[j] == "(":
                depth += 1
            elif expr[j] == ")":
                depth -= 1
            j += 1
        if depth:
            return expr
        content = expr[open_idx + 1 : j - 1]
        args: list[str] = []
        cur: list[str] = []
        dep = 0
        for ch in content:
            if ch == "(":
                dep += 1
            elif ch == ")":
                dep -= 1
            if ch == "," and dep == 0:
                args.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        args.append("".join(cur))
        args = [a.strip() for a in args if a.strip()]
        if not args:
            return expr
        joined = (" " + op + " ").join(f"({a})" for a in args)
        expr = expr[: target.start()] + joined + expr[j:]
    return expr


def _paren_balance(expr: str) -> int:
    """粗略括号配平：>0 表示缺 ')'，<0 表示多 ')'。DSL 不含字符串，直接计数即可。"""
    return expr.count("(") - expr.count(")")


_IMPLICIT_MA_RE = re.compile(r"\b(quote_volume|open|high|low|close|volume)_(\d+)\b")


def _expand_column_ma(expr: str, allowed: set[str]) -> str:
    """把 `volume_20` 这类“列名_周期”的臆造变量展开成该列的滚动均值。

    内置均线只覆盖 close（sma_* / ema_*），模型常想当然写 volume_20 / close_20 表示
    N 周期均线。这里仅当该名字尚未定义（不在 allowed）时展开为 `col.rolling(N).mean()`；
    已由用户在前文赋值过的同名变量保持不变。
    """
    return _IMPLICIT_MA_RE.sub(
        lambda m: m.group(0)
        if m.group(0) in allowed
        else f"{m.group(1)}.rolling({m.group(2)}).mean()",
        expr,
    )


def _indicator_series(df: pd.DataFrame) -> dict[str, pd.Series]:
    """常用技术指标序列（标准默认参数），注入沙箱 env 供表达式直接引用。

    命名即变量：macd / rsi / atr / boll_upper / sma_20 / ema_20 等，
    让模型无需手工用链式方法重算这些高频指标。
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    out: dict[str, pd.Series] = {}

    def ema(s: pd.Series, span: int) -> pd.Series:
        return s.ewm(span=span, adjust=False).mean()

    # MACD (12, 26, 9)
    macd = ema(close, 12) - ema(close, 26)
    macd_signal = ema(macd, 9)
    out["macd"] = macd
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd - macd_signal

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)

    # ATR(14)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()

    # 布林带 (20, 2)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["boll_mid"] = mid
    out["boll_upper"] = mid + 2 * std
    out["boll_lower"] = mid - 2 * std

    # 典型价（供 CCI 使用）
    typical = (high + low + close) / 3

    # KDJ / 随机指标 (14, 3, 3)
    ll = low.rolling(14).min()
    hh = high.rolling(14).max()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    d = k.rolling(3).mean()
    out["stoch_k"] = k
    out["stoch_d"] = d
    out["stoch_j"] = 3 * k - 2 * d

    # Williams %R(14)
    out["wr"] = -100 * (hh - close) / (hh - ll).replace(0, np.nan)

    # ROC(12)
    out["roc"] = (close / close.shift(12) - 1) * 100

    # CCI(20)
    tp_ma = typical.rolling(20).mean()
    mad = (typical - tp_ma).abs().rolling(20).mean()
    out["cci"] = (typical - tp_ma) / (0.015 * mad.replace(0, np.nan))

    # 常用均线（便捷序列）
    for n in (5, 10, 20, 60):
        out[f"sma_{n}"] = close.rolling(n).mean()
        out[f"ema_{n}"] = ema(close, n)

    return out


INDICATOR_NAMES: frozenset[str] = frozenset(
    {
        "macd", "macd_signal", "macd_hist",
        "rsi", "atr",
        "boll_mid", "boll_upper", "boll_lower",
        "stoch_k", "stoch_d", "stoch_j",
        "wr", "roc", "cci",
        "sma_5", "sma_10", "sma_20", "sma_60",
        "ema_5", "ema_10", "ema_20", "ema_60",
    }
)


class _Normalize(ast.NodeTransformer):
    """把 Python 逻辑/链式比较改写成 pandas 元素级运算。

    - `a and b` -> `a & b`，`a or b` -> `a | b`
    - `a < b < c` -> `(a < b) & (b < c)`
    - `&`/`|` 两侧若只有一边是布尔（如已赋值的 0/1 变量），自动把另一边转布尔再连接。
    """

    def __init__(self, bool_vars: set[str] | None = None) -> None:
        self._bool_vars = bool_vars or set()

    def _as_bool(self, node: ast.AST) -> ast.AST:
        return ast.Call(func=ast.Name(id="_asb", ctx=ast.Load()), args=[node], keywords=[])

    def _genuine_bool(self, node: ast.AST) -> bool:
        """结构上一定是布尔的结果（比较/布尔常量/位运算/not）。"""
        if isinstance(node, ast.Compare):
            return True
        if isinstance(node, ast.Constant):
            return isinstance(node.value, bool)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return True
        return False

    def _is_bool_var(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self._bool_vars

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        if len(node.values) < 2:
            return node
        op = ast.BitAnd if isinstance(node.op, ast.And) else ast.BitOr
        out = node.values[0]
        for v in node.values[1:]:
            out = ast.BinOp(left=out, op=op(), right=v)
        return out

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            return node
        # 只有至少一边是“布尔语义”时才做连接；纯数值 & 数值维持原样（让求值报错）
        has_bool = self._genuine_bool(node.left) or self._is_bool_var(node.left) or self._genuine_bool(node.right) or self._is_bool_var(node.right)
        if not has_bool:
            return node
        if not self._genuine_bool(node.left):
            node.left = self._as_bool(node.left)
        if not self._genuine_bool(node.right):
            node.right = self._as_bool(node.right)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) <= 1:
            return node
        parts = [
            ast.Compare(
                left=node.left if i == 0 else node.comparators[i - 1],
                ops=[o],
                comparators=[c],
            )
            for i, (o, c) in enumerate(zip(node.ops, node.comparators))
        ]
        out = parts[0]
        for p in parts[1:]:
            out = ast.BinOp(left=out, op=ast.BitAnd(), right=p)
        return out

    def visit_Call(self, node: ast.Call) -> ast.AST:
        """`ema_20(close)` / `boll_mid(20)` / `macd(12, 26, 9)` 这类“把内置指标
        当函数加参数”的笔误 → 直接当序列用（内置指标参数固定，实参只能是 close 或常量）。"""
        self.generic_visit(node)
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in INDICATOR_NAMES
            and node.args
            and not node.keywords
            and all(
                (isinstance(a, ast.Name) and a.id == "close")
                or (isinstance(a, ast.Constant) and isinstance(a.value, (int, float)))
                for a in node.args
            )
        ):
            return ast.copy_location(ast.Name(id=func.id, ctx=ast.Load()), node)
        return node


class _Checker(ast.NodeVisitor):
    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def visit(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Name):
            if node.id in FUNC_WHITELIST:
                return None
            if node.id not in self._allowed:
                hint = SPECIAL_NAME_HINT.get(node.id)
                extra = f"。{hint}" if hint else ""
                raise SandboxError(f"只允许使用 K 线列或已赋值变量，出现 {node.id!r}{extra}")
            return None
        if isinstance(node, ast.Constant):
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
            if isinstance(func, ast.Name):
                if func.id in FUNC_WHITELIST:
                    _fn, nargs = FUNC_WHITELIST[func.id]
                    if len(node.args) != nargs or node.keywords:
                        raise SandboxError(f"函数 {func.id}() 需要 {nargs} 个位置参数")
                    for a in node.args:
                        self.visit(a)
                    return None
                hint = SPECIAL_NAME_HINT.get(func.id)
                if hint:
                    raise SandboxError(f"不支持的函数 {func.id}（{hint}）")
                raise SandboxError(f"不支持直接调用 {func.id}()，请用 K 线列+白名单方法的链式写法")
            if not isinstance(func, ast.Attribute) or func.attr not in CALLABLE_ATTRS:
                raise SandboxError(
                    f"方法 {func.attr if isinstance(func, ast.Attribute) else '?'} 不在白名单，"
                    "支持：shift/rolling/mean/std/abs/...（请用 K 线列+白名单方法的链式写法）"
                )
            # 全局统计（无窗口）会跨时间使用未来信息，必须限定在滚动/指数窗口上。
            if func.attr in REDUCTION_ATTRS and not _is_windowed_receiver(func.value):
                raise SandboxError(
                    f"{func.attr}() 必须作用在滚动窗口上，例如 close.rolling(20).{func.attr}() "
                    f"或 close.ewm(span=12).{func.attr}()；直接对整个序列做全局统计会引入"
                    "未来函数（look-ahead），导致回测虚高。请改用滚动/指数窗口。"
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
        if isinstance(node, ast.Compare):
            if not all(isinstance(o, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.ops):
                raise SandboxError("不支持该比较运算")
            self.visit(node.left)
            for c in node.comparators:
                self.visit(c)
            return None
        raise SandboxError(f"不支持该表达式结构：{type(node).__name__}")


def _eval_single(
    expr: str,
    env: dict[str, Any],
    allowed: set[str],
    index: pd.Index,
    bool_vars: set[str],
) -> tuple[pd.Series, bool]:
    expr = _repair_conjunctions(expr)
    expr = _repair_logic_calls(expr)
    bal = _paren_balance(expr)
    if bal > 0:
        raise SandboxError(f"表达式括号不匹配：多出 {bal} 个 '('，缺少对应的 ')'")
    if bal < 0:
        raise SandboxError(f"表达式括号不匹配：多出 {-bal} 个 ')'")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SandboxError(
            f"表达式语法错误：{exc.msg}。请检查括号是否成对、运算符是否完整，"
            "并使用 K 线列 + 白名单链式方法（如 close.shift(5)、close.rolling(20).mean()）"
        ) from exc
    tree = _Normalize(bool_vars).visit(tree)
    ast.fix_missing_locations(tree)
    _Checker(allowed).visit(tree)
    try:
        code = compile(tree, "<factor>", "eval")
        result = eval(code, {"__builtins__": {}}, env)  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        raise SandboxError(f"求值失败：{exc}") from exc
    if isinstance(result, pd.Series):
        was_bool = result.dtype == bool
        out = result
    elif isinstance(result, np.ndarray):
        was_bool = result.dtype == bool
        out = pd.Series(result, index=index)
    else:
        raise SandboxError("表达式结果不是序列（请返回整列运算结果）")
    out = pd.to_numeric(out, errors="coerce").astype(float)
    return out.replace([float("inf"), float("-inf")], float("nan")), was_bool


def _clean_statement(st: str) -> str:
    """去掉模型常见的无害写法，便于宽松解析：行内注释与 `return` 前缀。

    本 DSL 不允许字符串，故 `#` 之后必为注释；`return` 是关键字，只会出现在行首。
    """
    hash_idx = st.find("#")
    if hash_idx != -1:
        st = st[:hash_idx]
    m = re.match(r"^\s*return\s+", st, flags=re.IGNORECASE)
    if m:
        st = st[m.end():]
    return st.strip()


def _split_assignment(st: str) -> tuple[str | None, str | None]:
    """若 st 是 `name = value` 形式的单条赋值，返回 (name, value_src)；否则 (None, None)。

    用 AST 判定，避免把 `==`/`!=`/`<=`/`>=` 或关键字参数（如 ewm(span=12)）误判为赋值。
    """
    try:
        tree = ast.parse(st, mode="exec")
    except SyntaxError:
        return None, None
    body = tree.body
    if len(body) != 1 or not isinstance(body[0], ast.Assign):
        return None, None
    targets = body[0].targets
    if len(targets) != 1 or not isinstance(targets[0], ast.Name):
        return None, None
    seg = ast.get_source_segment(st, body[0].value)
    if seg is None:
        return None, None
    return targets[0].id, seg


def safe_eval_expr(expr: str, df: pd.DataFrame) -> pd.Series:
    """校验并求值表达式/受限多语句 DSL，返回与 df.index 对齐的 pd.Series。"""
    expr = _normalize_pct(expr)
    env: dict[str, Any] = {name: fn for name, (fn, _n) in FUNC_WHITELIST.items()}
    allowed: set[str] = set(COLUMNS)
    for col in COLUMNS:
        if col in df.columns:
            env[col] = df[col]
    # 注入常用技术指标序列（如 macd/rsi/atr/obv/sma_20 等），供表达式直接引用
    for name, series in _indicator_series(df).items():
        env[name] = series
        allowed.add(name)

    statements = [s for s in re.split(r"[;\n]", expr) if s.strip()]
    last_series: pd.Series | None = None
    bool_vars: set[str] = set()
    for raw in statements:
        st = _clean_statement(raw)
        if not st:
            continue
        name, value_src = _split_assignment(st)
        if name is not None:
            if name in COLUMNS:
                raise SandboxError(f"不能用 {name} 作为变量名（保留列名）")
            series_val, was_bool = _eval_single(_expand_column_ma(value_src, allowed), env, allowed, df.index, bool_vars)
            env[name] = series_val
            allowed.add(name)
            if was_bool:
                bool_vars.add(name)
            last_series = series_val
        else:
            series_val, _was_bool = _eval_single(_expand_column_ma(st, allowed), env, allowed, df.index, bool_vars)
            last_series = series_val
    if last_series is None:
        raise SandboxError("没有可校验的表达式")
    return last_series


def _synthetic_frame(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """构造与真实 K 线同构的合成数据（含 quote_volume），用于表达式干跑校验。"""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0.0, 0.004, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(1e6, 2e6, n)
    quote_volume = volume * close
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "quote_volume": quote_volume,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1h"),
    )


def validate_expression(expr: str) -> tuple[bool, list[str], list[str]]:
    """对表达式做沙箱干跑（合成 K 线，不依赖真实行情）。

    返回 (ok, errors, warnings)：
    - ok=False 且 errors 非空 → 沙箱拒绝（语法/白名单/结构），调用方应重试。
    - ok=True 时 warnings 给出“能跑但区分度低”的提示（全 NaN / 近似恒定）。
    """
    try:
        df = _synthetic_frame()
        series = safe_eval_expr(expr, df)
    except SandboxError as exc:
        return False, [str(exc)], []
    except Exception as exc:  # noqa: BLE001
        return False, [f"求值异常：{exc}"], []
    warnings: list[str] = []
    if series.isna().all():
        warnings.append("表达式在合成数据上全为 NaN（窗口可能过长或逻辑恒空）")
    elif float(series.std() or 0.0) < 1e-12:
        warnings.append("因子在合成数据上几乎恒定（标准差≈0），区分度低")
    return True, [], warnings
