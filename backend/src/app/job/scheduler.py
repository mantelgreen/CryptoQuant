"""Beat 调度器：扫描 scheduled_jobs，命中 cron 时派发增量同步到 ARQ worker。

进程模型（与 03/04 设计一致）：
    python -m app.job.scheduler

每 60s 检查一次所有启用的定时任务，按 5 段式 cron 判断当前分钟是否命中；
命中则对每个交易对 enqueue `sync_incremental_klines_task`（走 data worker），
并回写 last_run_at。同分钟幂等（last_run_at >= 本分钟起点则跳过）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from arq import create_pool
from arq.connections import RedisSettings
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..core.config import get_settings
from ..core.cron import cron_matches
from ..core.db import SessionLocal
from ..core.logging import configure_logging, get_logger
from ..data.repository.kline_repo import ScheduledJobRepository

logger = get_logger(__name__)
settings = get_settings()


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


async def _dispatch_due() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB 存 naive UTC，保持兼容
    floor = _floor_minute(now)
    dispatched = 0
    async with SessionLocal() as session:
        repo = ScheduledJobRepository(session)
        schedules = await repo.list_enabled()
        if not schedules:
            return
        due: list[dict] = []
        for s in schedules:
            last = s.get("last_run_at")
            if last is not None and last.replace(tzinfo=None) >= floor:
                continue
            if not cron_matches(s["cron"], now):
                continue
            due.append(s)

        if not due:
            return

        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            for s in due:
                for symbol in s.get("symbols", []):
                    try:
                        await redis.enqueue_job(
                            "sync_incremental_klines_task",
                            symbol,
                            s["interval"],
                            _queue_name=settings.JOB_QUEUE_DATA,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("schedule.enqueue_failed", schedule_id=str(s["id"]), symbol=symbol, error=str(exc))
                        continue
                await repo.touch_run(s["id"], last_run_at=now)
                dispatched += 1
                logger.info(
                    "schedule.dispatched",
                    schedule_id=str(s["id"]),
                    name=s.get("name"),
                    symbols=s.get("symbols"),
                    interval=s.get("interval"),
                )
        finally:
            await redis.aclose()
    if dispatched:
        logger.info("schedule.round", dispatched=dispatched, at=now.isoformat())


async def _dispatch_sim_auto() -> None:
    """把已启用、到期的“策略自动账户”派发给 worker 执行一次自动决策。"""
    from ..simtrader.service import SimAccountRepository

    now = datetime.now(timezone.utc)
    due: list[dict] = []
    async with SessionLocal() as session:
        repo = SimAccountRepository(session)
        accts = await repo.list()
        for a in accts:
            if a.get("account_type") != "strategy":
                continue
            cfg = a.get("config") or {}
            if cfg.get("enabled", True) is False:
                continue
            cadence = int(cfg.get("cadence_min") or 5)
            last = cfg.get("last_run_at")
            last_dt = None
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                except ValueError:
                    last_dt = None
            if last_dt and (now - last_dt).total_seconds() < cadence * 60:
                continue
            due.append((a, cfg))
        if not due:
            return
        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            for a, cfg in due:
                await redis.enqueue_job(
                    "sim_auto_tick_task",
                    str(a["id"]),
                    _queue_name=settings.JOB_QUEUE_DATA,
                )
                cfg["last_run_at"] = now.isoformat()
                await repo.update_config(a["id"], cfg)
                logger.info("sim.auto.dispatched", account_id=str(a["id"]), name=a.get("name"))
        finally:
            await redis.aclose()


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        _dispatch_due,
        trigger=IntervalTrigger(seconds=60),
        id="schedule_dispatch",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=90,
    )
    sched.add_job(
        _dispatch_sim_auto,
        trigger=IntervalTrigger(seconds=60),
        id="sim_auto_dispatch",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=90,
    )
    return sched


async def _main() -> None:
    configure_logging(level=settings.APP_LOG_LEVEL, json_output=settings.APP_ENV != "dev")
    logger.info("beat.start", interval_s=60)
    scheduler = build_scheduler()
    scheduler.start()
    # 立即跑一轮，便于验收
    await asyncio.sleep(1)
    try:
        await _dispatch_due()
    except Exception as exc:  # noqa: BLE001
        logger.warning("beat.initial_round_failed", error=str(exc))
    try:
        await asyncio.Event().wait()  # 常驻
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(_main())
