"""数据服务（M0 占位，仅暴露交易所调用入口给上层）。"""
from __future__ import annotations

from typing import Any

from ...exchange.adapters import BinanceRestClient


class MarketDataService:
    """封装 BinanceRestClient，提供 ticker / klines / orderbook 的高层调用。

    M0 阶段不做缓存，全部走上游；M1 起接入 Redis 缓存。
    """

    def __init__(self) -> None:
        self._client = BinanceRestClient()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_tickers(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return await self._client.get_tickers(symbol=symbol)

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list[Any]]:
        return await self._client.get_klines(
            symbol=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit,
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        return await self._client.get_orderbook(symbol=symbol, limit=limit)
