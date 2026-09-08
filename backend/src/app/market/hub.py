"""StreamHub v3 —— Binance 实时行情动态枢纽（同进程直接 fanout）。

- 网关与 Hub 同进程：解码后事件 **直接投递给本地订阅队列**（低延迟、无 Redis 依赖），
  同时旁路 publish 到 Redis `ws:market:{param}` 供未来多网关扩展。
- 订阅集合变化时快速重建连接（手动 close + transport.abort，不等待上游握手）。
- 订阅类型：trade / kline_{interval} / depth20@100ms / ticker。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosedError

from ..core.config import get_settings
from ..core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"
MAX_STREAMS_PER_CONN = 180

_DEPTH_LEVELS = 20
_DEPTH_MS = 100


def depth_param(symbol: str) -> str:
    return f"{symbol.lower()}@depth{_DEPTH_LEVELS}@{_DEPTH_MS}ms"


def kline_param(symbol: str, interval: str) -> str:
    return f"{symbol.lower()}@kline_{interval}"


def trade_param(symbol: str) -> str:
    return f"{symbol.lower()}@trade"


def ticker_param(symbol: str) -> str:
    return f"{symbol.lower()}@ticker"


class _Interest:
    __slots__ = ("params", "queue")

    def __init__(self, params: list[str]) -> None:
        self.params = set(params)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4000)


class StreamHub:
    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._change = asyncio.Event()
        self._desired: set[str] = set()
        self._warm: set[str] = set()
        self._interests: list[_Interest] = []
        self._local: dict[str, set[_Interest]] = {}
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run())
            logger.info("streamhub.started")

    async def stop(self) -> None:
        self._stop_event.set()
        self._change.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        logger.info("streamhub.stopped")

    # ===== 订阅接口 =====
    def warm(self, params: list[str]) -> None:
        self._warm.update(params)
        self._recalc()

    def add(self, params: list[str]) -> _Interest:
        it = _Interest(params)
        self._interests.append(it)
        self._bind(it)
        self._recalc()
        return it

    def update(self, it: _Interest, params: list[str]) -> None:
        self._unbind(it)
        it.params = set(params)
        self._bind(it)
        self._recalc()

    def remove(self, it: _Interest) -> None:
        if it in self._interests:
            self._interests.remove(it)
        self._unbind(it)
        self._recalc()
        it.queue.put_nowait({"_done": True})

    def _bind(self, it: _Interest) -> None:
        for p in it.params:
            self._local.setdefault(p, set()).add(it)

    def _unbind(self, it: _Interest) -> None:
        for p, bucket in list(self._local.items()):
            bucket.discard(it)
            if not bucket:
                self._local.pop(p, None)

    def _recalc(self) -> None:
        desired = set(self._warm)
        for it in self._interests:
            desired |= it.params
        if desired != self._desired:
            self._desired = desired
            self._change.set()

    # ===== 连接核心 =====
    async def _run(self) -> None:
        backoff = 1.0
        ws = None
        try:
            while not self._stop_event.is_set():
                params = sorted(self._desired)
                if not params:
                    self._change.clear()
                    try:
                        await asyncio.wait_for(self._change.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                    backoff = 1.0
                    continue
                if len(params) > MAX_STREAMS_PER_CONN:
                    logger.warning("streamhub.too_many_streams", count=len(params))

                url = f"{BINANCE_WS_BASE}?streams={'/'.join(params)}"
                try:
                    ws = await websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=12)
                except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
                    logger.warning("streamhub.connect_failed", error=str(exc), backoff=round(backoff, 2))
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 20.0)
                    continue

                logger.info("streamhub.connected", streams=len(params))
                backoff = 1.0
                base = set(params)
                try:
                    while not self._stop_event.is_set():
                        if set(self._desired) != base:
                            logger.info("streamhub.reconnecting", before=len(base), after=len(self._desired))
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.4)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if "error" in payload:
                            logger.warning("streamhub.binance_error", payload=payload)
                            continue
                        stream = payload.get("stream")
                        data = payload.get("data") or {}
                        if stream:
                            self._fanout(stream, data)
                except (ConnectionClosedError, OSError, websockets.WebSocketException) as exc:
                    logger.warning("streamhub.disconnected", error=str(exc))
                    await asyncio.sleep(min(backoff, 2.0))
                finally:
                    # 快速拆卸：仅优雅 close（带短超时），不做 transport.abort，
                    # 以免触发 websockets 17 在 connection_made 前回调 connection_lost 的竞态
                    if ws is not None:
                        try:
                            await asyncio.wait_for(ws.close(close_timeout=0.4), timeout=1.0)
                        except Exception:  # noqa: BLE001
                            pass
                        ws = None
        finally:
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.close(close_timeout=0.4), timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
        logger.info("streamhub.run_stopped")

    # ===== 解码 + 分发 =====
    def _fanout(self, stream: str, data: dict[str, Any]) -> None:
        symbol, _, suffix = stream.partition("@")
        symbol = symbol.upper()
        ev: dict[str, Any] | None = None
        if suffix.startswith("kline_"):
            k = data.get("k") or {}
            ev = {
                "type": "kline",
                "symbol": symbol,
                "interval": suffix[6:],
                "bar": {
                    "t": k.get("t"), "o": k.get("o"), "h": k.get("h"),
                    "l": k.get("l"), "c": k.get("c"), "v": k.get("v"),
                    "closed": bool(k.get("x")), "T": k.get("T"),
                },
            }
        elif suffix == "trade":
            ev = {
                "type": "trade",
                "symbol": symbol,
                "trade": {
                    "id": data.get("t"), "time": data.get("T"),
                    "price": float(data.get("p") or 0), "qty": float(data.get("q") or 0),
                    "side": "sell" if data.get("m") else "buy",
                },
            }
        elif suffix.startswith("depth"):
            ev = {
                "type": "depth",
                "symbol": symbol,
                "depth": {"lastUpdateId": data.get("lastUpdateId"), "bids": data.get("bids") or [], "asks": data.get("asks") or []},
            }
        elif suffix == "ticker":
            ev = {
                "type": "ticker",
                "symbol": symbol,
                "ticker": {
                    "lastPrice": data.get("c"), "openPrice": data.get("o"),
                    "highPrice": data.get("h"), "lowPrice": data.get("l"),
                    "priceChangePercent": data.get("P"), "quoteVolume": data.get("q"),
                    "bidPrice": data.get("b"), "askPrice": data.get("a"),
                },
            }
        if ev is None:
            return

        # 本地直投（主路径，同进程）
        for it in list(self._local.get(stream, set())):
            try:
                it.queue.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    it.queue.get_nowait()
                    it.queue.put_nowait(ev)
                except asyncio.QueueEmpty:
                    pass


_hub: StreamHub | None = None


def get_stream_hub() -> StreamHub:
    global _hub
    if _hub is None:
        _hub = StreamHub()
    return _hub


async def start_default_hub(symbols: list[str], interval: str = "1m") -> StreamHub:  # noqa: ARG001
    hub = get_stream_hub()
    if hub._task is None or hub._task.done():
        warm: list[str] = []
        for sym in symbols:
            warm.append(trade_param(sym))
            warm.append(ticker_param(sym))
        hub.warm(warm)
        await hub.start()
    return hub


async def stop_default_hub() -> None:
    global _hub
    if _hub is not None:
        await _hub.stop()
        _hub = None
