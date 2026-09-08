"""Binance 适配器注册与导出。

业务代码通过 `from app.exchange import get_adapter; get_adapter('binance')` 获取。
"""
from __future__ import annotations

from .binance.rest import BinanceRestClient
from .registry import register_adapter

register_adapter("binance", lambda: BinanceRestClient())

__all__ = ["BinanceRestClient", "register_adapter"]
