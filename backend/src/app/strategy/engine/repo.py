"""strategy 域数据访问（策略 / 回测 / 资金曲线 / 成交）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, desc, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...data.models.orm import (
    backtest_trades_table,
    backtests_table,
    equity_curves_table,
    strategy_versions_table,
    strategies_table,
)


class StrategyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = strategies_table()

    async def create(self, *, name: str, description: str | None = None, tags: list[str] | None = None) -> UUID:
        sid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=sid, name=name, description=description, tags=tags or []
            )
        )
        await self.db.commit()
        return sid

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def get(self, sid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == sid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def delete(self, sid: UUID) -> None:
        await self.db.execute(self._t.delete().where(self._t.c.id == sid))
        await self.db.commit()


class StrategyVersionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = strategy_versions_table()

    async def create(
        self,
        *,
        strategy_id: UUID,
        code: str,
        params: dict[str, Any] | None = None,
        risk_config: dict[str, Any] | None = None,
    ) -> UUID:
        # 找当前最大 version +1
        result = await self.db.execute(
            select(self._t.c.version)
            .where(self._t.c.strategy_id == strategy_id)
            .order_by(desc(self._t.c.version))
            .limit(1)
        )
        cur = result.scalar_one_or_none()
        version = (cur or 0) + 1
        vid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=vid,
                strategy_id=strategy_id,
                version=version,
                code=code,
                params=params or {},
                risk_config=risk_config or {},
            )
        )
        await self.db.commit()
        return vid

    async def latest(self, strategy_id: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(
            select(self._t)
            .where(self._t.c.strategy_id == strategy_id)
            .order_by(desc(self._t.c.version))
            .limit(1)
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def get(self, vid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == vid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_for(self, strategy_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t)
            .where(self._t.c.strategy_id == strategy_id)
            .order_by(desc(self._t.c.version))
            .limit(limit)
        )
        return [dict(row._mapping) for row in result]


class BacktestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = backtests_table()
        self._eq = equity_curves_table()
        self._tr = backtest_trades_table()

    async def create(
        self,
        *,
        strategy_id: UUID,
        version_id: UUID,
        symbols: list[str],
        interval: str,
        start_time: datetime,
        end_time: datetime,
        params: dict[str, Any] | None = None,
    ) -> UUID:
        bid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=bid,
                strategy_id=strategy_id,
                version_id=version_id,
                symbols=symbols,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                params=params or {},
                status="pending",
            )
        )
        await self.db.commit()
        return bid

    async def update_status(
        self,
        bid: UUID,
        *,
        status: str,
        progress_pct: int | None = None,
        stage: str | None = None,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if progress_pct is not None:
            values["progress_pct"] = progress_pct
        if stage is not None:
            values["stage"] = stage
        if summary is not None:
            values["summary"] = summary
        if error is not None:
            values["error"] = error
        if status == "running":
            values["started_at"] = datetime.utcnow()
        if status in ("succeeded", "failed"):
            values["finished_at"] = datetime.utcnow()
        await self.db.execute(
            self._t.update().where(self._t.c.id == bid).values(**values)
        )
        await self.db.commit()

    async def get(self, bid: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(select(self._t).where(self._t.c.id == bid))
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def save_equity(self, run_type: str, run_id: UUID, points: list[tuple[datetime, float]]) -> None:
        if not points:
            return
        rows = [
            {"run_type": run_type, "run_id": run_id, "ts": ts, "equity": float(v)}
            for ts, v in points
        ]
        stmt = pg_insert(self._eq).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["run_type", "run_id", "ts"]
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def save_trades(self, backtest_id: UUID, trades: list[dict[str, Any]]) -> None:
        if not trades:
            return
        rows = [
            {
                "backtest_id": backtest_id,
                "ts": datetime.fromisoformat(str(t["ts"])) if isinstance(t["ts"], str) else t["ts"],
                "symbol": t.get("symbol", ""),
                "side": t.get("side", "buy"),
                "price": float(t["price"]),
                "qty": float(t["qty"]),
                "fee": float(t.get("fee", 0)),
                "pnl": float(t.get("pnl", 0)),
                "reason": None,
            }
            for t in trades
        ]
        await self.db.execute(self._tr.insert().values(rows))
        await self.db.commit()

    async def get_equity(self, run_type: str, run_id: UUID, limit: int = 2000) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._eq)
            .where(and_(self._eq.c.run_type == run_type, self._eq.c.run_id == run_id))
            .order_by(self._eq.c.ts.asc())
            .limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def get_trades(self, backtest_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._tr)
            .where(self._tr.c.backtest_id == backtest_id)
            .order_by(self._tr.c.ts.asc())
            .limit(limit)
        )
        return [dict(row._mapping) for row in result]
