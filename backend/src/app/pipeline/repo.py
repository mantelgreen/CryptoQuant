"""pipeline 数据访问（AI 流水线 run / round）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.json_sanitize import json_safe
from ..data.models.orm import pipeline_rounds_table, pipeline_runs_table


class PipelineRunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = pipeline_runs_table()

    async def create(self, *, name: str, config: dict[str, Any]) -> UUID:
        rid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=rid,
                name=name,
                config=json_safe(config),
                status="pending",
                factor_pool=[],
            )
        )
        await self.db.commit()
        return rid

    async def get(self, rid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == rid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def delete(self, rid: UUID) -> bool:
        result = await self.db.execute(self._t.delete().where(self._t.c.id == rid))
        await self.db.commit()
        return (result.rowcount or 0) > 0

    async def update_status(
        self,
        rid: UUID,
        *,
        status: str,
        current_round: int | None = None,
        best_model_id: UUID | None = None,
        best_metrics: dict[str, Any] | None = None,
        factor_pool: list[str] | None = None,
        stall_count: int | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if current_round is not None:
            values["current_round"] = current_round
        if best_model_id is not None:
            values["best_model_id"] = best_model_id
        if best_metrics is not None:
            values["best_metrics"] = json_safe(best_metrics)
        if factor_pool is not None:
            values["factor_pool"] = factor_pool
        if stall_count is not None:
            values["stall_count"] = stall_count
        if error is not None:
            values["error"] = error
        if status == "running":
            values["started_at"] = datetime.utcnow()
        if status in ("succeeded", "finished", "failed", "stopped"):
            values["finished_at"] = datetime.utcnow()
        stmt = self._t.update().where(self._t.c.id == rid)
        if status == "running":
            # 运行中的轮次会多次写 running；若期间用户点了停止（已置终态），
            # 不能被这些 running 更新覆盖回 running，否则停止失效、无法重新启动。
            stmt = stmt.where(
                self._t.c.status.notin_(("stopped", "failed", "succeeded", "finished"))
            )
        await self.db.execute(stmt.values(**values))
        await self.db.commit()

    async def set_best(
        self, rid: UUID, *, model_id: UUID, metrics: dict[str, Any]
    ) -> None:
        await self.db.execute(
            self._t.update()
            .where(self._t.c.id == rid)
            .values(best_model_id=model_id, best_metrics=json_safe(metrics), stall_count=0)
        )
        await self.db.commit()


class PipelineRoundRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = pipeline_rounds_table()

    async def create(self, *, pipeline_run_id: UUID, round_no: int) -> UUID:
        rid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=rid,
                pipeline_run_id=pipeline_run_id,
                round_no=round_no,
                phase="gen",
                status="pending",
            )
        )
        await self.db.commit()
        return rid

    async def get(self, rid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == rid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def get_by_no(
        self, pipeline_run_id: UUID, round_no: int
    ) -> dict[str, Any] | None:
        result = await self.db.execute(
            select(self._t).where(
                self._t.c.pipeline_run_id == pipeline_run_id,
                self._t.c.round_no == round_no,
            )
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_for(self, pipeline_run_id: UUID) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t)
            .where(self._t.c.pipeline_run_id == pipeline_run_id)
            .order_by(self._t.c.round_no.asc())
        )
        return [dict(row._mapping) for row in result]

    async def update(
        self,
        rid: UUID,
        *,
        phase: str | None = None,
        status: str | None = None,
        factors_generated: list[dict[str, Any]] | None = None,
        factors_picked: list[dict[str, Any]] | None = None,
        train_result: dict[str, Any] | None = None,
        backtest_summary: dict[str, Any] | None = None,
        diagnosis: dict[str, Any] | None = None,
        model_id: UUID | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if phase is not None:
            values["phase"] = phase
        if status is not None:
            values["status"] = status
        if factors_generated is not None:
            values["factors_generated"] = json_safe(factors_generated)
        if factors_picked is not None:
            values["factors_picked"] = json_safe(factors_picked)
        if train_result is not None:
            values["train_result"] = json_safe(train_result)
        if backtest_summary is not None:
            values["backtest_summary"] = json_safe(backtest_summary)
        if diagnosis is not None:
            values["diagnosis"] = json_safe(diagnosis)
        if model_id is not None:
            values["model_id"] = model_id
        if error is not None:
            values["error"] = error
        if values:
            await self.db.execute(
                self._t.update().where(self._t.c.id == rid).values(**values)
            )
            await self.db.commit()
