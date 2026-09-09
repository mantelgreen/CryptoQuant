"""策略 / 回测 服务层：API 只做参数与错误映射，业务编排（建记录 + 入队 worker）在这。
供后续把服务层加密打包时隔离。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .repo import BacktestRepository, StrategyRepository, StrategyVersionRepository


async def start_backtest(
    db: AsyncSession,
    *,
    strategy_id: UUID,
    version_id: UUID | None,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    params: dict[str, Any],
) -> dict[str, Any]:
    """创建回测记录并投递到 ARQ worker（data 队列）。抛 ValueError(code) 供 API 映射 HTTP 错误。"""
    if end <= start:
        raise ValueError("end_must_be_after_start")

    srepo = StrategyRepository(db)
    strategy = await srepo.get(strategy_id)
    if not strategy:
        raise ValueError("strategy_not_found")

    vrepo = StrategyVersionRepository(db)
    version = await vrepo.get(version_id) if version_id else await vrepo.latest(strategy_id)
    if not version:
        raise ValueError("strategy_version_not_found")

    template_id = (params or {}).get("template_id") or (version.get("params") or {}).get("template_id")
    if not template_id:
        raise ValueError("template_id_required_in_params")

    brepo = BacktestRepository(db)
    bid = await brepo.create(
        strategy_id=strategy_id,
        version_id=version["id"],
        symbols=[symbol],
        interval=interval,
        start_time=start,
        end_time=end,
        params={**params, "template_id": template_id},
    )

    from arq import create_pool
    from arq.connections import RedisSettings

    from ...core.config import get_settings

    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await redis.enqueue_job(
            "run_backtest_task",
            str(bid),
            str(strategy_id),
            str(version["id"]),
            symbol,
            interval,
            start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(),
            {**params, "template_id": template_id},
            template_id,
            _queue_name=settings.JOB_QUEUE_DATA,
        )
    finally:
        await redis.aclose()

    return {"backtest_id": str(bid), "status": "queued"}
