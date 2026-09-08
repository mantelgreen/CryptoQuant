"""回测 ARQ 任务。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID


async def run_backtest_task(
    ctx,  # noqa: ARG001
    backtest_id: str,
    strategy_id: str,
    version_id: str,
    symbol: str,
    interval: str,
    start_iso: str,
    end_iso: str,
    params: dict,
    template_id: str,
) -> None:
    from ..strategy.engine.job import run_backtest_job

    await run_backtest_job(
        UUID(backtest_id),
        UUID(strategy_id),
        UUID(version_id),
        symbol,
        interval,
        datetime.fromisoformat(start_iso),
        datetime.fromisoformat(end_iso),
        params,
        template_id,
    )
