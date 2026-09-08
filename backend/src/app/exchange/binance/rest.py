"""Binance REST 客户端。

特性：
- 复用 core.rate_limit 的令牌桶
- 与币安 `X-MBX-USED-WEIGHT-1m` 响应头校准
- 429/418 触发熔断
- httpx.AsyncClient + 连接池
"""
from __future__ import annotations

from typing import Any

import httpx

from ...core.config import get_settings
from ...core.rate_limit import get_binance_limiter
from ..base import ExchangeAdapter


class BinanceRestClient(ExchangeAdapter):
    name = "binance"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self._limiter = get_binance_limiter()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self._settings.BA_BASE_URL,
                "timeout": httpx.Timeout(self._settings.BA_REQUEST_TIMEOUT),
                "headers": {"User-Agent": "CryptoQuant/0.1"},
                "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
            }
            if self._settings.BA_PROXY_URL:
                kwargs["proxies"] = self._settings.BA_PROXY_URL
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, *, weight: int = 1, **kwargs: Any) -> httpx.Response:
        await self._limiter.acquire(scope="binance", weight=weight)
        client = await self._ensure_client()
        resp = await client.request(method, path, **kwargs)

        # 与币安 header 校准
        used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
        if used:
            try:
                # 若本地已超 header，触发熔断
                if int(used) >= self._limiter.bucket.cap_per_min:
                    self._limiter.trip_breaker(30.0)
            except ValueError:
                pass

        if resp.status_code in (418, 429):
            # 已被官方封禁
            self._limiter.trip_breaker(60.0)

        resp.raise_for_status()
        return resp

    # ===== Adapter 实现 =====

    async def get_symbols(self) -> list[dict[str, Any]]:
        # exchangeInfo 权重 20
        resp = await self._request("GET", "/api/v3/exchangeInfo", weight=20)
        data = resp.json()
        return data.get("symbols", [])

    async def get_tickers(self, symbol: str | None = None) -> list[dict[str, Any]]:
        path = "/api/v3/ticker/24hr"
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        # 单 symbol 权重 1，全量 80
        weight = 1 if symbol else 80
        resp = await self._request("GET", path, weight=weight, params=params)
        data = resp.json()
        if isinstance(data, dict):
            return [data]
        return data

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        # klines 权重 2
        resp = await self._request("GET", self._settings.KLINE_ENDPOINT, weight=2, params=params)
        return resp.json()

    async def get_orderbook(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        # 权重映射：100→1, 500→5, 1000→10, 5000→20
        weight = {100: 1, 500: 5, 1000: 10, 5000: 20}.get(limit, 1)
        resp = await self._request(
            "GET",
            "/api/v3/depth",
            weight=weight,
            params={"symbol": symbol, "limit": limit},
        )
        return resp.json()
