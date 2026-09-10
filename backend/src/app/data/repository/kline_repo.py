"""K 线 / 下载任务 / 调度仓库（M1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, desc, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ..models.orm import download_jobs_table, klines_table, scheduled_jobs_table


def _capped_limit(limit: int | None) -> int | None:
    """limit=None（不限制）时用配置的全局上限兜底，防止长窗口一次性读入过多行。

    `KLINE_QUERY_MAX_ROWS<=0` 表示真正不限制；显式传入的 limit 原样返回。
    """
    if limit is not None:
        return limit
    cap = int(get_settings().KLINE_QUERY_MAX_ROWS or 0)
    return cap if cap > 0 else None

# interval -> 秒（用于判断“目标粒度是否能由底库粒度整除聚合”）
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}

# interval -> time_bucket 的间隔字面量（保证桶对齐语义与币安一致）
_BUCKET_LITERAL: dict[str, str] = {
    "1m": "1 minute", "3m": "3 minutes", "5m": "5 minutes", "15m": "15 minutes",
    "30m": "30 minutes", "1h": "1 hour", "2h": "2 hours", "4h": "4 hours",
    "6h": "6 hours", "8h": "8 hours", "12h": "12 hours",
    "1d": "1 day", "3d": "3 days", "1w": "1 week",
}


class KlineRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = klines_table()

    async def get_last_bar_ts(self, symbol: str, interval: str) -> datetime | None:
        stmt = (
            select(self._t.c.open_time)
            .where(
                and_(
                    self._t.c.symbol == symbol,
                    self._t.c.interval == interval,
                )
            )
            .order_by(desc(self._t.c.open_time))
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(self._t).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "interval", "open_time"])
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount or 0

    async def query(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = _capped_limit(limit)
        stmt = (
            select(self._t)
            .where(
                and_(
                    self._t.c.symbol == symbol,
                    self._t.c.interval == interval,
                    self._t.c.open_time >= start,
                    self._t.c.open_time <= end,
                )
            )
            .order_by(self._t.c.open_time.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.db.execute(stmt)
        return [dict(row._mapping) for row in result]

    async def coverage(self, symbol: str) -> list[dict[str, Any]]:
        """该交易对各颗粒度的本地数据覆盖：interval / count / min_time / max_time。"""
        stmt = (
            select(
                self._t.c.interval,
                func.count().label("count"),
                func.min(self._t.c.open_time).label("min_time"),
                func.max(self._t.c.open_time).label("max_time"),
            )
            .where(self._t.c.symbol == symbol)
            .group_by(self._t.c.interval)
            .order_by(self._t.c.interval)
        )
        result = await self.db.execute(stmt)
        return [dict(row._mapping) for row in result]

    async def _has_rows(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> bool:
        stmt = select(func.count()).select_from(self._t).where(
            and_(
                self._t.c.symbol == symbol,
                self._t.c.interval == interval,
                self._t.c.open_time >= start,
                self._t.c.open_time <= end,
            )
        )
        result = await self.db.execute(stmt)
        return (result.scalar_one() or 0) > 0

    async def query_or_aggregate(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """优先按存储粒度精确查询；若该粒度原始数据不全，则用更细粒度底库做桶聚合。

        limit=None 表示不截断（默认），返回窗口内全部数据；传入正整数则只取最早 limit 根。

        例如：ETH 只存了 30 根 1d（2026-08-09 起），但 1m 底库完整覆盖到 2025-01，
        请求 1d 时发现 1d 开始得比 1m 晚 → 改由 1m 聚合，保证“更早历史”不丢。
        """
        target_sec = _INTERVAL_SECONDS.get(interval)
        if target_sec is None or interval not in _BUCKET_LITERAL:
            return []
        limit = _capped_limit(limit)

        cov = await self.coverage(symbol)
        cov_min = {row["interval"]: row.get("min_time") for row in cov}
        cands: list[str] = []
        for row in cov:
            cand = row["interval"]
            cand_sec = _INTERVAL_SECONDS.get(cand)
            if cand_sec and cand_sec <= target_sec and target_sec % cand_sec == 0:
                cands.append(cand)
        cands.sort(key=lambda c: _INTERVAL_SECONDS[c])

        exact = await self.query(symbol, interval, start, end, limit=limit)

        async def _pick_base() -> str | None:
            for cand in cands:
                if await self._has_rows(symbol, cand, start, end):
                    return cand
            return None

        if exact:
            exact_min = exact[0]["open_time"]
            # 是否有“开始得比该粒度更早”的细底库（说明原始记录只覆盖了后段）
            older = [c for c in cands if cov_min.get(c) is not None and cov_min[c] < exact_min]
            if not older:
                return exact
            # 优先用最细的、窗口内有数据的旧底库聚合；都没有则退回精确数据
            base = await _pick_base()
            if base is None or base not in older:
                return exact
        else:
            base = await _pick_base()
            if base is None:
                return []

        # bucket 来自内部白名单 _BUCKET_LITERAL，可安全内插
        # （参数化会触发 asyncpg 把字符串当 interval 类型编码而报错）
        bucket = _BUCKET_LITERAL[interval]
        limit_clause = "LIMIT :limit" if limit is not None else ""
        sql = text(
            f"""
            SELECT
              :sym                          AS symbol,
              :interval                     AS "interval",
              bucket                        AS open_time,
              (array_agg(open ORDER BY open_time))[1]   AS open,
              max(high)                     AS high,
              min(low)                      AS low,
              (array_agg(close ORDER BY open_time DESC))[1] AS close,
              sum(volume)                   AS volume,
              sum(quote_volume)             AS quote_volume,
              sum(trades_count)             AS trades_count,
              sum(taker_buy_base)           AS taker_buy_base,
              sum(taker_buy_quote)          AS taker_buy_quote,
              max(close_time)               AS close_time,
              count(*)                      AS bars
            FROM (
              SELECT *, time_bucket('{bucket}', open_time) AS bucket
              FROM klines
              WHERE symbol = :sym
                AND interval = :base
                AND open_time >= :start
                AND open_time <= :end
            ) s
            GROUP BY bucket
            ORDER BY bucket ASC
            {limit_clause}
            """
        )
        params: dict[str, Any] = {
            "sym": symbol,
            "interval": interval,
            "base": base,
            "start": start,
            "end": end,
        }
        if limit is not None:
            params["limit"] = limit
        result = await self.db.execute(sql, params)
        return [dict(row._mapping) for row in result]

    async def query_latest(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """取窗口内“最新 limit 根”后按时间正序（图表用：长历史优先看近期）。

        同样处理“原始粒度不完整，需由更细底库聚合补全”的场景：
        若某粒度的原始数据只覆盖后段，而更细底库覆盖更早，则走桶聚合的最近 limit 根。
        """
        target_sec = _INTERVAL_SECONDS.get(interval)
        if target_sec is None or interval not in _BUCKET_LITERAL:
            return []

        cov = await self.coverage(symbol)
        cov_min = {row["interval"]: row.get("min_time") for row in cov}
        cands: list[str] = []
        for row in cov:
            cand = row["interval"]
            cand_sec = _INTERVAL_SECONDS.get(cand)
            if cand and cand_sec and cand_sec <= target_sec and target_sec % cand_sec == 0:
                cands.append(cand)
        cands.sort(key=lambda c: _INTERVAL_SECONDS[c])

        # 决定 base / 是否聚合
        base: str | None = None
        agg = True
        raw_min = cov_min.get(interval)
        if raw_min is not None:
            older = [c for c in cands if c != interval and cov_min.get(c) is not None and cov_min[c] < raw_min]
            if older:
                for c in cands:
                    if c in older and await self._has_rows(symbol, c, start, end):
                        base = c
                        break
                if base is None:
                    agg = False
            else:
                agg = False
        else:
            for c in cands:
                if await self._has_rows(symbol, c, start, end):
                    base = c
                    break
            if base is None:
                return []

        if not agg:
            # 原始数据最新 limit 根
            result = await self.db.execute(
                select(self._t)
                .where(
                    and_(
                        self._t.c.symbol == symbol,
                        self._t.c.interval == interval,
                        self._t.c.open_time >= start,
                        self._t.c.open_time <= end,
                    )
                )
                .order_by(desc(self._t.c.open_time))
                .limit(limit)
            )
            rows = [dict(r._mapping) for r in result]
            rows.reverse()
            return rows

        # 桶聚合：先取最新 limit 根再反转为正序
        bucket = _BUCKET_LITERAL[interval]
        sql = text(
            f"""
            SELECT
              :sym                          AS symbol,
              :interval                     AS "interval",
              bucket                        AS open_time,
              (array_agg(open ORDER BY open_time))[1]   AS open,
              max(high)                     AS high,
              min(low)                      AS low,
              (array_agg(close ORDER BY open_time DESC))[1] AS close,
              sum(volume)                   AS volume,
              sum(quote_volume)             AS quote_volume,
              sum(trades_count)             AS trades_count,
              sum(taker_buy_base)           AS taker_buy_base,
              sum(taker_buy_quote)          AS taker_buy_quote,
              max(close_time)               AS close_time,
              count(*)                      AS bars
            FROM (
              SELECT *, time_bucket('{bucket}', open_time) AS bucket
              FROM klines
              WHERE symbol = :sym
                AND interval = :base
                AND open_time >= :start
                AND open_time <= :end
            ) s
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT :limit
            """
        )
        result = await self.db.execute(
            sql,
            {
                "sym": symbol,
                "interval": interval,
                "base": base,
                "start": start,
                "end": end,
                "limit": limit,
            },
        )
        rows = [dict(r._mapping) for r in result]
        rows.reverse()
        return rows


class DownloadJobRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = download_jobs_table()

    async def create(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        mode: str = "full",
    ) -> UUID:
        job_id = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=job_id,
                symbol=symbol,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                mode=mode,
                status="pending",
            )
        )
        await self.db.commit()
        return job_id

    async def get(self, job_id: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(
            select(self._t).where(self._t.c.id == job_id)
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).order_by(desc(self._t.c.created_at)).limit(limit)
        )
        return [dict(row._mapping) for row in result]

    async def update_status(
        self,
        job_id: UUID,
        *,
        status: str,
        downloaded_bars: int | None = None,
        total_bars: int | None = None,
        gaps: list[Any] | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if downloaded_bars is not None:
            values["downloaded_bars"] = downloaded_bars
        if total_bars is not None:
            values["total_bars"] = total_bars
        if gaps is not None:
            values["gaps"] = gaps
        if error is not None:
            values["error"] = error
        if status == "running":
            values["started_at"] = datetime.utcnow()
        if status in ("succeeded", "failed"):
            values["finished_at"] = datetime.utcnow()
        await self.db.execute(
            update(self._t).where(self._t.c.id == job_id).values(**values)
        )
        await self.db.commit()


class ScheduledJobRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = scheduled_jobs_table()

    async def create(
        self,
        *,
        name: str,
        cron: str,
        symbols: list[str],
        interval: str,
        enabled: bool = True,
    ) -> UUID:
        job_id = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=job_id,
                name=name,
                cron=cron,
                symbols=symbols,
                interval=interval,
                enabled=enabled,
            )
        )
        await self.db.commit()
        return job_id

    async def list_enabled(self) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(self._t).where(self._t.c.enabled.is_(True))
        )
        return [dict(row._mapping) for row in result]

    async def get(self, job_id: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(
            select(self._t).where(self._t.c.id == job_id)
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def delete(self, job_id: UUID) -> None:
        await self.db.execute(
            self._t.delete().where(self._t.c.id == job_id)
        )
        await self.db.commit()

    async def set_enabled(self, job_id: UUID, enabled: bool) -> None:
        await self.db.execute(
            update(self._t)
            .where(self._t.c.id == job_id)
            .values(enabled=enabled)
        )
        await self.db.commit()

    async def touch_run(self, job_id: UUID, *, last_run_at: datetime, next_run_at: datetime | None = None) -> None:
        values: dict[str, Any] = {"last_run_at": last_run_at}
        if next_run_at is not None:
            values["next_run_at"] = next_run_at
        await self.db.execute(
            update(self._t).where(self._t.c.id == job_id).values(**values)
        )
        await self.db.commit()
