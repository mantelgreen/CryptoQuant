"""交易所适配层抽象。

不同交易所（binance / okx / bybit）都应实现 ExchangeAdapter 接口。
业务代码只依赖该抽象，避免硬编码交易所 API。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExchangeAdapter(ABC):
    name: str

    @abstractmethod
    async def get_symbols(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_tickers(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list[Any]]: ...

    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 100) -> dict[str, Any]: ...
