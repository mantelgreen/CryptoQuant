"""前端 WS 路由：/ws/market（实时行情网关，经 Hub 本地队列直投）。

协议（文本 JSON）：
  客户端 → 服务端
    {type:"auth", token:"..."}        首包 5s 内鉴权（当前占位，M2 接 JWT）
    {type:"sub",  channels:[{symbol,kind,interval?}, ...]}  声明订阅（全量替换）
    {type:"ping", ts}
  服务端 → 客户端
    {type:"ready", channels:[...]}
    {type:"batch", items:[<行情事件>]}   100ms 节流批量下发
    {type:"pong",  ts}

kind ∈ trade | kline | depth | ticker
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.logging import get_logger
from .hub import depth_param, get_stream_hub, kline_param, ticker_param, trade_param

router = APIRouter()
logger = get_logger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")
_KINDS = {"trade", "kline", "depth", "ticker"}
_INTERVALS = {"1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}


def _auth_ok(_payload: dict[str, Any]) -> bool:
    return bool(_payload.get("token"))


def _to_param(ch: dict[str, Any]) -> str | None:
    sym = str(ch.get("symbol", "")).strip().upper()
    kind = str(ch.get("kind", "")).strip().lower()
    if not _SYMBOL_RE.match(sym) or kind not in _KINDS:
        return None
    if kind == "kline":
        interval = str(ch.get("interval", "")).strip()
        if interval not in _INTERVALS:
            return None
        return kline_param(sym, interval)
    if kind == "trade":
        return trade_param(sym)
    if kind == "depth":
        return depth_param(sym)
    if kind == "ticker":
        return ticker_param(sym)
    return None


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        if first.get("type") != "auth" or not _auth_ok(first):
            await websocket.close(code=4401)
            return
    except asyncio.TimeoutError:
        await websocket.close(code=4408)
        return
    except Exception:  # noqa: BLE001
        await websocket.close(code=4400)
        return

    hub = get_stream_hub()
    interest = hub.add([])
    buffer: list[dict[str, Any]] = []
    last_flush = time.time()

    await websocket.send_json({"type": "ready", "channels": []})
    logger.info("ws.market.connected")

    try:
        while True:
            ws_task = asyncio.create_task(websocket.receive_text())
            q_task = asyncio.create_task(interest.queue.get())
            done, pending = await asyncio.wait({ws_task, q_task}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

            if ws_task in done:
                try:
                    payload = json.loads(ws_task.result())
                except Exception:  # noqa: BLE001
                    payload = {}
                mtype = payload.get("type")
                if mtype == "ping":
                    await websocket.send_json({"type": "pong", "ts": payload.get("ts") or time.time()})
                elif mtype == "sub":
                    params = [p for p in (_to_param(ch) for ch in payload.get("channels", [])) if p]
                    hub.update(interest, params)
                    await websocket.send_json({"type": "ready", "channels": params})
                    logger.info("ws.market.sub", channels=params)

            if q_task in done and not q_task.cancelled():
                try:
                    ev = q_task.result()
                    if ev.get("_done"):
                        break
                    buffer.append(ev)
                except asyncio.QueueEmpty:
                    pass

            if buffer and (time.time() - last_flush) * 1000 >= 100:
                await websocket.send_json({"type": "batch", "items": buffer})
                buffer.clear()
                last_flush = time.time()
    except WebSocketDisconnect:
        logger.info("ws.market.disconnected")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws.market.error", error=str(exc))
    finally:
        hub.remove(interest)
