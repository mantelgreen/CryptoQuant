"""ARQ worker 配置 + 任务注册。

启动：
    arq app.job.arq_app.WorkerSettings

多个队列可以跑多个 worker 实例。
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from arq.connections import RedisSettings

from ..core.config import get_settings
from ..core.db import SessionLocal
from ..data.tasks.download import run_download_job, run_incremental_sync
from .backtest import run_backtest_task

settings = get_settings()


async def download_klines_task(ctx, job_id: str, symbol: str, interval: str, start_iso: str, end_iso: str):
    """ARQ 任务：下载 K 线。

    ARQ 不直接支持 datetime/UUID 参数，故用字符串。
    """
    await run_download_job(
        UUID(job_id),
        symbol,
        interval,
        datetime.fromisoformat(start_iso),
        datetime.fromisoformat(end_iso),
    )


async def sync_incremental_klines_task(ctx, symbol: str, interval: str):  # noqa: ARG001
    """ARQ 任务：增量同步某交易对到当前（定时任务核心）。"""
    await run_incremental_sync(symbol, interval)


async def sim_auto_tick_task(ctx, account_id: str):  # noqa: ARG001
    """ARQ 任务：策略自动账户执行一次自动决策（由 beat 定时派发）。"""
    from uuid import UUID

    from ..simtrader.service import SimAccountRepository, run_auto_tick

    async with SessionLocal() as session:
        account = await SimAccountRepository(session).get(UUID(account_id))
        if account:
            await run_auto_tick(session, account)


async def run_pipeline_round_task(ctx, run_id: str, round_no: int):
    """ARQ 任务：AI 流水线执行一轮（一轮一 job，跑完自行入队下一轮）。"""
    from ..pipeline.worker import run_pipeline_round_task as _impl

    await _impl(ctx, run_id, round_no)


class WorkerSettings:
    functions = [
        download_klines_task,
        sync_incremental_klines_task,
        run_backtest_task,
        sim_auto_tick_task,
        run_pipeline_round_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 3
    job_timeout = 1800  # 30 分钟
    keep_result = 3600
    queue_name = settings.JOB_QUEUE_DATA

    @staticmethod
    async def on_startup(ctx):
        ctx["started_at"] = datetime.utcnow().isoformat()
