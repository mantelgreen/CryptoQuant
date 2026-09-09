"""因子注册表：可选地按 code → 元数据 + 计算函数 注册“随产品内置”的因子。

当前产品以用户 / AI 生成的表达式因子为主（因子行自带 expression，经沙箱求值），
因此这里不再内置任何示例因子，注册表保持为空。

保留该注册机制是为了今后若需要“随产品分发、不能删除的内置代码因子”时，
用 register_factors() 声明即可，get_factors/compute_factor 等调用方无需改动。
"""
from __future__ import annotations

from typing import Any


def register_factors() -> dict[str, dict[str, Any]]:
    return {}


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
    for meta in get_factors().values():
        item = {k: v for k, v in meta.items() if not exclude_fn or k != "fn"}
        items.append(item)
    return items


def compute_factor(code: str, df: Any) -> Any:
    meta = get_factor(code)
    if meta is None:
        raise KeyError(f"unknown_factor:{code}")
    return meta["fn"](df).astype(float)
