"""交易所适配注册表。

通过 `register_adapter(name, factory)` 注册，运行期通过 `get_adapter(name)` 获取。
"""
from __future__ import annotations

from typing import Callable

from .base import ExchangeAdapter

_registry: dict[str, Callable[[], ExchangeAdapter]] = {}


def register_adapter(name: str, factory: Callable[[], ExchangeAdapter]) -> None:
    _registry[name] = factory


def get_adapter(name: str) -> ExchangeAdapter:
    if name not in _registry:
        raise KeyError(f"exchange_adapter_not_registered:{name}")
    return _registry[name]()


def supported_adapters() -> list[str]:
    return list(_registry.keys())
