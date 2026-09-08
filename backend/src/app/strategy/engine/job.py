"""回测任务执行（被 ARQ worker 调用）。

流程：加载 klines → 选 symbol → 向量化回测 → 存 equity / trades / summary。
每个关键阶段向 Redis `ws:backtest:{id}` 发布进度，供网关 SSE 实时推送。
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import pandas as pd

from ...core.config import get_settings
from ...core.db import SessionLocal
from ...core.logging import get_logger
from ...core.redis import get_redis
from ...data.repository.kline_repo import KlineRepository
from ..engine.repo import BacktestRepository
from ..engine.runner import BacktestConfig, run_backtest_vectorized
from ..engine.templates import get_templates

logger = get_logger(__name__)
settings = get_settings()


async def _publish_progress(
    backtest_id: UUID,
    *,
    status: str,
    stage: str | None = None,
    pct: int | None = None,
    error: str | None = None,
    summary: dict | None = None,
) -> None:
    payload = {
        "type": "progress",
        "backtest_id": str(backtest_id),
        "status": status,
        "stage": stage,
        "pct": pct,
        "error": error,
        "summary": summary,
    }
    try:
        r = get_redis()
        await r.publish(f"ws:backtest:{backtest_id}", json.dumps(payload, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.debug("backtest.progress_publish_failed", error=str(exc))


async def run_backtest_job(
    backtest_id: UUID,
    strategy_id: UUID,
    version_id: UUID,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    params: dict,
    template_id: str,
) -> None:
    """执行一次回测并写库。"""
    async with SessionLocal() as session:
        repo = BacktestRepository(session)
        await repo.update_status(backtest_id, status="running", progress_pct=5, stage="loading_data")
        await _publish_progress(backtest_id, status="running", stage="loading_data", pct=5)
        try:
            # 1. 加载 K 线（支持自动桶聚合：库里只有 15m 时也能回测 30m/1h/1d）
            kline_repo = KlineRepository(session)
            rows = await kline_repo.query_or_aggregate(symbol, interval, start, end, limit=20000)
            if not rows:
                cover = await kline_repo.coverage(symbol)
                have = ", ".join(f"{r['interval']}×{r['count']}" for r in cover) or "无"
                raise RuntimeError(
                    f"窗口内无 {interval} 数据。该币本地已有：{have}。"
                    "可先到「K 线下载」补齐该区间，或把时间窗改到已有数据范围内。"
                )
            df = pd.DataFrame(rows)
            df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
            for c in ("open", "high", "low", "close", "volume"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            await repo.update_status(backtest_id, status="running", progress_pct=35, stage="backtesting")
            await _publish_progress(backtest_id, status="running", stage="backtesting", pct=35)

            # 2. 回测
            template_meta = get_templates().get(template_id, {})
            allow_short = bool(template_meta.get("long_short", False))
            cfg = BacktestConfig(initial_cash=100_000.0, fee_rate=0.001, allow_short=allow_short)
            result = run_backtest_vectorized(
                ohlcv=df, template_id=template_id, params=params, cfg=cfg, interval=interval
            )
            await repo.update_status(backtest_id, status="running", progress_pct=80, stage="persisting")
            await _publish_progress(backtest_id, status="running", stage="persisting", pct=80)

            # 3. 写 equity 曲线（抽样，避免太多行）
            equity = result["equity"]
            step = max(1, len(equity) // 1500)
            pts = [(pd.Timestamp(ts).to_pydatetime(), float(v)) for ts, v in equity.iloc[::step].items()]
            await repo.save_equity("backtest", backtest_id, pts)

            trades = result["trades"]
            for t in trades:
                t["symbol"] = symbol
            await repo.save_trades(backtest_id, trades)

            # 4. summary
            summary = {
                "template_id": template_id,
                "symbol": symbol,
                "interval": interval,
                "metrics": result["metrics"],
                "final_equity": float(equity.iloc[-1]) if len(equity) else None,
                "bars": len(equity),
            }
            await repo.update_status(
                backtest_id, status="succeeded", progress_pct=100, stage="done", summary=summary
            )
            await _publish_progress(
                backtest_id, status="succeeded", stage="done", pct=100, summary=summary
            )
            logger.info("backtest.done", backtest_id=str(backtest_id), final=str(summary.get("final_equity")))
        except Exception as exc:
            logger.error("backtest.failed", backtest_id=str(backtest_id), error=str(exc))
            await repo.update_status(backtest_id, status="failed", error=str(exc))
            await _publish_progress(backtest_id, status="failed", error=str(exc))
            raise
