"""Binance K 线下载器。

封装 core.rate_limit + 分页 + 增量续传 + 完整性校验。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ...core.logging import get_logger
from ...exchange.binance.rest import BinanceRestClient
from ..repository.kline_repo import KlineRepository

logger = get_logger(__name__)


# interval -> 秒（用于分页切片步长）
_INTERVAL_SECONDS: dict[str, int] = {
    "1s": 1, "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000,
}

_PAGE_BARS = 1000                     # 币安单请求上限
_EMPTY_FASTSKIP_BIG_MS = 30 * 24 * 3600 * 1000   # 连续空页后的大跨步（约 30 天）
_EMPTY_BEFORE_FASTSKIP = 5             # 连续空页达到该值才启用快跳（避免吞掉真实小缺口）
_RETRY_TIMES = 4                       # 单页失败重试次数
_MAX_PAGES = 400_000                   # 兜底：防止异常空区间无限循环


def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class BinanceKlineFetcher:
    """K 线下载器。"""

    def __init__(self, client: BinanceRestClient | None = None, repo: KlineRepository | None = None) -> None:
        self.client = client or BinanceRestClient()
        self.repo = repo

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _fetch_with_retry(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int) -> list:
        """单页拉取 + 指数退避重试（429/网络/5xx）。"""
        last_exc: Exception | None = None
        for attempt in range(_RETRY_TIMES):
            try:
                raw = await self.client.get_klines(
                    symbol=symbol, interval=interval, start_ms=start_ms, end_ms=end_ms, limit=limit
                )
                if raw is None:
                    raise RuntimeError("empty_response")
                return raw
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "kline.fetch.retry",
                    symbol=symbol, interval=interval, page_start_ms=start_ms,
                    attempt=attempt + 1, error=str(exc),
                )
                await asyncio.sleep(min(2 ** attempt, 8))  # 1s/2s/4s/8s
        raise RuntimeError(f"kline_fetch_failed_after_retries:{last_exc}")

    async def download_range(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        on_progress=None,
    ) -> dict[str, Any]:
        """下载 [start, end] 区间的 K 线到仓库，带逐段校验 + 失败重试 + 空区间快跳。

        币安约束：单请求最多 1000 根（无“仅一月”限制），长区间必须翻页。
        校验：用“上一根开盘点 + 1 步长”检查连续性，缺口写入 gaps；
        空页连续出现时用约 30 天的大跨步快跳，避免对无历史区间打海量无效请求。
        节奏：每页后 50ms 缓冲；重试用指数退避，规避限速/封 IP。

        返回 {"downloaded": int, "gaps": list[(start,end)]}
        """
        if interval not in _INTERVAL_SECONDS:
            raise ValueError(f"unsupported_interval:{interval}")
        step_seconds = _INTERVAL_SECONDS[interval]
        step_ms = step_seconds * 1000
        page_span_ms = step_ms * _PAGE_BARS

        cur_ms = _to_ms(start)
        end_ms = _to_ms(end)
        if cur_ms >= end_ms:
            return {"downloaded": 0, "gaps": []}

        downloaded = 0
        gaps: list[dict[str, Any]] = []
        prev_last_open_ms: int | None = None
        empty_run = 0
        pages = 0

        if self.repo is None:
            raise RuntimeError("kline_repository_required")

        while cur_ms < end_ms and pages < _MAX_PAGES:
            pages += 1
            page_end_ms = min(cur_ms + page_span_ms, end_ms)
            logger.info(
                "kline.fetch.page", symbol=symbol, interval=interval,
                cur_ms=cur_ms, page_end_ms=page_end_ms, page=pages,
            )

            # —— 取一段 + 失败退避重试 ——
            raw = await self._fetch_with_retry(symbol, interval, cur_ms, page_end_ms, _PAGE_BARS)

            # —— 空页处理：仅当“尚未取到任何数据”（早于上市区间）才 30 天快跳，
            #    否则按算术步进（保留真实缺口检测语义）——
            if not raw:
                empty_run += 1
                if empty_run >= _EMPTY_BEFORE_FASTSKIP and prev_last_open_ms is None:
                    cur_ms = page_end_ms + _EMPTY_FASTSKIP_BIG_MS
                    empty_run = 0
                    logger.info("kline.fetch.fastskip", symbol=symbol, interval=interval, next_ms=cur_ms)
                else:
                    cur_ms = page_end_ms + 1
                continue

            empty_run = 0
            rows: list[dict[str, Any]] = []
            for k in raw:
                open_ms = int(k[0])
                open_time = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
                # —— 逐段校验：与前一根是否连续，不连续记缺口 ——
                if prev_last_open_ms is not None:
                    expected_ms = prev_last_open_ms + step_ms
                    if open_ms > expected_ms:
                        gaps.append(
                            {
                                "from_ms": expected_ms,
                                "to_ms": open_ms - step_ms,
                            }
                        )
                prev_last_open_ms = open_ms
                rows.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "open_time": open_time,
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "quote_volume": float(k[7]) if len(k) > 7 else None,
                        "trades_count": int(k[8]) if len(k) > 8 else None,
                        "taker_buy_base": float(k[9]) if len(k) > 9 else None,
                        "taker_buy_quote": float(k[10]) if len(k) > 10 else None,
                        "close_time": datetime.fromtimestamp(int(k[6]) / 1000, tz=timezone.utc)
                        if len(k) > 6
                        else None,
                    }
                )

            inserted = await self.repo.bulk_insert(rows)
            downloaded += inserted
            if on_progress is not None:
                await on_progress(downloaded)

            # 下一页起点 = 最后一根开盘 + 1 步长（天然跳过缺口，语义与币安一致）
            cur_ms = prev_last_open_ms + step_ms + 1
            # 节奏控制：每页 50ms 缓冲 + 每 50 页额外 0.5s 平滑，避免突发触发 418
            await asyncio.sleep(0.05)
            if pages % 50 == 0:
                await asyncio.sleep(0.5)

        if pages >= _MAX_PAGES:
            logger.warning("kline.fetch.max_pages_hit", symbol=symbol, interval=interval)

        return {"downloaded": downloaded, "gaps": gaps}

    async def download_incremental(
        self,
        symbol: str,
        interval: str,
        on_progress=None,
    ) -> dict[str, Any]:
        """从最后一根之后增量同步到 now。"""
        if self.repo is None:
            raise RuntimeError("kline_repository_required")
        last = await self.repo.get_last_bar_ts(symbol, interval)
        now = datetime.now(tz=timezone.utc)
        if last is None:
            # 无历史时仅拉最近 30 天
            start = now - timedelta(days=30)
        else:
            start = last + timedelta(seconds=_INTERVAL_SECONDS[interval])
        if start >= now:
            return {"downloaded": 0, "gaps": []}
        return await self.download_range(symbol, interval, start, now, on_progress)
