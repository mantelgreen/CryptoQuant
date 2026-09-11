"""AI 流水线 ARQ 任务：一轮一个 job，跑完自行入队下一轮（规避 ARQ 30 分钟 job_timeout）。"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.logging import get_logger
from .repo import PipelineRunRepository
from .service import publish_progress, run_round

logger = get_logger(__name__)
settings = get_settings()

_TERMINAL = ("succeeded", "finished", "failed", "stopped")


async def run_pipeline_round_task(ctx, run_id: str, round_no: int):  # noqa: ARG001
    async with SessionLocal() as session:
        rrun = PipelineRunRepository(session)
        run = await rrun.get(UUID(run_id))
        if run is None:
            return
        if run["status"] in _TERMINAL:
            return
        if run["status"] == "pending":
            await rrun.update_status(run["id"], status="running")

        try:
            result = await run_round(session, run, round_no)
        except Exception as exc:  # noqa: BLE001
            logger.error("pipeline.round_failed", run_id=run_id, round_no=round_no, error=str(exc))
            await rrun.update_status(run["id"], status="failed", error=str(exc))
            await publish_progress(run_id, status="failed", round_no=round_no, error=str(exc))
            return

        run = await rrun.get(UUID(run_id))
        if run is None:
            return
        if run["status"] in ("stopped", "failed"):
            return

        if result.get("terminate"):
            final = "succeeded" if result.get("succeeded") else "finished"
            await rrun.update_status(run["id"], status=final, current_round=round_no)
            await publish_progress(run_id, status=final, round_no=round_no)
            return

        max_rounds = int((run["config"] or {}).get("max_rounds") or 1)
        run_mode = str((run["config"] or {}).get("run_mode") or "rounds").lower()
        # 固定轮次模式：跑满 max_rounds 即结束；定时任务模式无轮次上限（靠停止/优秀终止）
        if run_mode != "schedule" and round_no >= max_rounds:
            await rrun.update_status(run["id"], status="finished", current_round=round_no)
            await publish_progress(run_id, status="finished", round_no=round_no)
            return

        await rrun.update_status(run["id"], status="running", current_round=round_no)

        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            defer: dict[str, object] = {}
            if run_mode == "schedule":
                interval_min = int((run["config"] or {}).get("interval_min") or 60)
                defer["_defer_by"] = timedelta(minutes=max(1, interval_min))
            await redis.enqueue_job(
                "run_pipeline_round_task",
                run_id,
                round_no + 1,
                _queue_name=settings.JOB_QUEUE_DATA,
                **defer,
            )
        finally:
            await redis.aclose()
