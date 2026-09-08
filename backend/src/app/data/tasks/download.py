"""下载任务的 async 接口（M1 / M1.5）。

- run_download_job：处理一条下载任务记录（full 拉区间 / incremental 自增到当前）。
- run_incremental_sync：无任务记录的增量同步（定时任务/调度器直接使用）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ...core.db import SessionLocal
from ...core.logging import get_logger
from ..repository.kline_repo import DownloadJobRepository, KlineRepository
from ..service.kline_fetcher import BinanceKlineFetcher

logger = get_logger(__name__)


async def run_download_job(
    job_id: UUID,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> None:
    """同步入口（被 ARQ worker 调用）。

    维护自己的 DB session（不依赖 request scope）。mode=incremental 时忽略
    传入起止时间，从库内最后一根续到 now（无历史则近 30 天）。
    """
    async with SessionLocal() as session:
        repo = DownloadJobRepository(session)
        kline_repo = KlineRepository(session)
        job = await repo.get(job_id)
        mode = (job or {}).get("mode", "full")
        await repo.update_status(job_id, status="running")
        try:
            fetcher = BinanceKlineFetcher(repo=kline_repo)
            try:

                async def _on_progress(downloaded: int) -> None:
                    await repo.update_status(job_id, status="running", downloaded_bars=downloaded)

                if mode == "incremental":
                    logger.info("kline.job.incremental", job_id=str(job_id), symbol=symbol, interval=interval)
                    result = await fetcher.download_incremental(
                        symbol, interval, on_progress=_on_progress
                    )
                else:
                    result = await fetcher.download_range(
                        symbol, interval, start, end, on_progress=_on_progress
                    )
                await repo.update_status(
                    job_id,
                    status="succeeded",
                    downloaded_bars=result["downloaded"],
                    gaps=result["gaps"] or None,
                )
                logger.info("kline.download.done", job_id=str(job_id), bars=result["downloaded"])
            finally:
                await fetcher.aclose()
        except Exception as exc:
            detail = str(exc) or f"{type(exc).__name__}: {exc!r}"
            logger.error("kline.download.failed", job_id=str(job_id), error=detail)
            await repo.update_status(job_id, status="failed", error=detail[:2000])
            raise


async def run_incremental_sync(symbol: str, interval: str) -> dict[str, Any]:
    """无任务记录地增量同步一段（供定时调度直接调用），返回 {downloaded, gaps}。"""
    async with SessionLocal() as session:
        repo = KlineRepository(session)
        fetcher = BinanceKlineFetcher(repo=repo)
        try:
            result = await fetcher.download_incremental(symbol, interval)
        finally:
            await fetcher.aclose()
    logger.info("kline.sync.done", symbol=symbol, interval=interval, bars=result["downloaded"])
    return result
