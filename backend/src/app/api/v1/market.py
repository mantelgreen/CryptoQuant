"""/api/v1/market 行情接口（M0：ticker / orderbook / klines 转发）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ...core.errors import UpstreamError
from ...data.service.market_data import MarketDataService

router = APIRouter(prefix="/market", tags=["market"])


def get_market_service() -> MarketDataService:
    return MarketDataService()


@router.get("/tickers")
async def list_tickers(
    symbol: str | None = Query(default=None, description="为空时返回全量"),
    svc: MarketDataService = Depends(get_market_service),
) -> dict[str, Any]:
    try:
        data = await svc.list_tickers(symbol=symbol)
    except Exception as exc:
        raise UpstreamError("binance_ticker_failed", detail={"reason": str(exc)}) from exc
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/orderbook/{symbol}")
async def orderbook(
    symbol: str,
    limit: int = Query(default=100, ge=50, le=5000),
    svc: MarketDataService = Depends(get_market_service),
) -> dict[str, Any]:
    try:
        data = await svc.get_orderbook(symbol=symbol, limit=limit)
    except Exception as exc:
        raise UpstreamError("binance_orderbook_failed", detail={"reason": str(exc)}) from exc
    return {"code": "OK", "message": "ok", "data": data}


@router.get("/klines")
async def klines(
    symbol: str = Query(..., min_length=1),
    interval: str = Query(..., min_length=1),
    limit: int = Query(default=500, ge=1, le=1000),
    svc: MarketDataService = Depends(get_market_service),
) -> dict[str, Any]:
    try:
        data = await svc.get_klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as exc:
        raise UpstreamError("binance_klines_failed", detail={"reason": str(exc)}) from exc
    return {"code": "OK", "message": "ok", "data": data}
