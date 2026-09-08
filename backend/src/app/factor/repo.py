"""因子 IC 研究结果仓库（M2.1：落库以便回看与跟踪）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.models.orm import factor_ic_runs_table, ml_models_table


class FactorRunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = factor_ic_runs_table()

    async def insert(
        self,
        *,
        code: str,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        ic: float | None,
        rank_ic: float | None,
        decay: dict[str, float | None],
        n: int,
    ) -> None:
        await self.db.execute(
            self._t.insert().values(
                id=uuid4(),
                code=code,
                symbol=symbol,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                ic=ic,
                rank_ic=rank_ic,
                ic_h1=decay.get("h_1"),
                ic_h3=decay.get("h_3"),
                ic_h5=decay.get("h_5"),
                n=n,
            )
        )
        await self.db.commit()

    async def latest(self, limit: int = 200) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def by_code(self, code: str, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t)
            .where(self._t.c.code == code)
            .order_by(desc(self._t.c.created_at))
            .limit(limit)
        )
        return [dict(row._mapping) for row in result]


class MlModelRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = ml_models_table()

    async def create(
        self,
        *,
        name: str,
        model_type: str,
        interval: str,
        horizon: int,
        symbols: list[str],
        features: list[dict[str, Any]],
        metrics: dict[str, Any],
        n_train: int,
        n_test: int,
    ) -> UUID:
        mid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=mid,
                name=name,
                model_type=model_type,
                interval=interval,
                horizon=horizon,
                symbols=symbols,
                features=features,
                metrics=metrics,
                n_train=n_train,
                n_test=n_test,
            )
        )
        await self.db.commit()
        return mid

    async def get(self, mid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == mid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

class UserFactorRepository:
    def __init__(self, db: AsyncSession) -> None:
        from ..data.models.orm import user_factors_table

        self.db = db
        self._t = user_factors_table()

    async def create(self, *, name, description, expression, source_model=None, tags=None) -> UUID:
        fid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=fid, name=name, description=description or "", expression=expression,
                tags=tags or ["AI"], source_model=source_model, status="draft",
            )
        )
        await self.db.commit()
        return fid

    async def list(self) -> list[dict[str, Any]]:
        from sqlalchemy import desc as _desc

        rows = await self.db.execute(select(self._t).order_by(_desc(self._t.c.created_at)))
        return [dict(r._mapping) for r in rows]

    async def delete(self, fid: UUID) -> None:
        await self.db.execute(self._t.delete().where(self._t.c.id == fid))
        await self.db.commit()
