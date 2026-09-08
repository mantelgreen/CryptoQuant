"""5 段式 cron 匹配工具（分 时 日 月 周）。

支持：`*`、`*/n`、`a-b`、`a-b/n`、`a,b,c` 与数字。
周字段 0-6（0=周日）；兼容 7=周日。月字段 1-12；日字段 1-31。
本实现用于 beat 按“当前分钟”判定是否命中，避免引入 croniter 依赖。
"""
from __future__ import annotations

from datetime import datetime

_FIELDS: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]] = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sun)
)


def _expand(field: str, lo: int, hi: int, *, dow: bool = False) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        base = part
        if "/" in part:
            base, _, step_s = part.partition("/")
            step = int(step_s)
        if base == "*":
            rng = (lo, hi)
        elif "-" in base:
            a_s, _, b_s = base.partition("-")
            a, b = int(a_s), int(b_s)
            rng = (a, b)
        else:
            v = int(base)
            rng = (v, v)
        a, b = rng
        if dow and b == 7:
            b = 6
        a = max(a, lo)
        b = min(b, hi)
        if a > b:
            continue
        for v in range(a, b + 1, step):
            if dow and v == 7:
                v = 0
            out.add(v)
    return out


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = [p.strip() for p in expr.split()]
    if len(parts) != 5:
        raise ValueError(f"invalid_cron:{expr}")
    parsed = []
    for (lo, hi), field, is_dow in zip(_FIELDS, parts, (False, False, False, False, True), strict=True):
        parsed.append(_expand(field, lo, hi, dow=is_dow))
    return (parsed[0], parsed[1], parsed[2], parsed[3], parsed[4])  # type: ignore[return-value]


def cron_matches(expr: str, dt: datetime) -> bool:
    minute, hour, dom, month, dow = parse_cron(expr)
    if dt.minute not in minute:
        return False
    if dt.hour not in hour:
        return False
    if dt.month not in month:
        return False

    parts = expr.split()
    dom_any = parts[2].strip() == "*"
    dow_any = parts[4].strip() == "*"
    dom_hit = dt.day in dom
    # cron 周字段 0=周日；Python weekday() 0=周一，需换算
    dow_hit = (dt.weekday() + 1) % 7 in dow

    if dom_any and dow_any:
        return True
    if dom_any:
        return dow_hit
    if dow_any:
        return dom_hit
    # 日期与星期都有限制时，命中其一即可（标准 cron 语义）
    return dom_hit or dow_hit
