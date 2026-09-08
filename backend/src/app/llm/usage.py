"""LLM 用量记录与聚合（R8：所有模型调用 token 汇总）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.models.orm import llm_costs_table, llm_usage_table


async def _unit_costs(db: AsyncSession) -> dict[tuple[str, str], tuple[float, float]]:
    """(provider, model) -> (prompt_per_1k, completion_per_1k)（取最新生效价）。"""
    out: dict[tuple[str, str], tuple[float, float]] = {}
    _t = llm_costs_table()
    rows = await db.execute(
        text(
            "SELECT DISTINCT ON (provider, model) provider, model, prompt_per_1k, completion_per_1k "
            "FROM llm_costs ORDER BY provider, model, effective_at DESC"
        )
    )
    for r in rows:
        try:
            out[(str(r[0]), str(r[1]))] = (float(r[2] or 0), float(r[3] or 0))
        except (TypeError, ValueError):
            continue
    return out


async def record_usage(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    caller: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    latency_ms: int,
) -> None:
    """记录一次调用到 llm_usage（含按 llm_costs 单价折算的成本）。"""
    _t = llm_usage_table()
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    total = prompt_tokens + completion_tokens

    costs = await _unit_costs(db)
    pp, cp = costs.get((provider, model), (0.0, 0.0))
    cost_usd = prompt_tokens / 1000.0 * pp + completion_tokens / 1000.0 * cp

    await db.execute(
        _t.insert().values(
            ts=datetime.now(timezone.utc),
            provider=provider,
            model=model,
            caller=caller,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_usd=round(cost_usd, 6),
            is_estimated=False,
            latency_ms=int(latency_ms or 0),
        )
    )
    await db.commit()


async def overview(db: AsyncSession) -> dict[str, Any]:
    _t = llm_usage_table()

    def _stmt(start: datetime | None = None):
        stmt = select(
            func.count().label("calls"),
            func.coalesce(func.sum(_t.c.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(_t.c.cost_usd), 0).label("cost"),
        )
        if start:
            stmt = stmt.where(_t.c.ts >= start)
        return stmt

    async def _agg(start: datetime | None = None) -> dict[str, Any]:
        row = (await db.execute(_stmt(start))).first()
        return {"calls": int(row[0]), "tokens": int(row[1]), "cost": round(float(row[2]), 4)}

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    by_model_rows = await db.execute(
        select(
            _t.c.provider, _t.c.model, func.count().label("calls"),
            func.sum(_t.c.total_tokens).label("tokens"),
            func.sum(_t.c.cost_usd).label("cost"),
            func.avg(_t.c.latency_ms).label("latency"),
        )
        .group_by(_t.c.provider, _t.c.model)
        .order_by(func.sum(_t.c.total_tokens).desc())
    )
    by_model = [
        {
            "provider": r[0], "model": r[1], "calls": int(r[2]),
            "tokens": int(r[3]), "cost": round(float(r[4] or 0), 4),
            "latency_ms": int(r[5] or 0),
        }
        for r in by_model_rows
    ]

    by_caller_rows = await db.execute(
        select(_t.c.caller, func.count().label("calls"), func.sum(_t.c.total_tokens).label("tokens"))
        .group_by(_t.c.caller)
        .order_by(func.sum(_t.c.total_tokens).desc())
    )
    by_caller = [
        {"caller": r[0], "calls": int(r[1]), "tokens": int(r[2])} for r in by_caller_rows
    ]

    return {
        "today": await _agg(day_start),
        "total": await _agg(),
        "by_model": by_model,
        "by_caller": by_caller,
    }


async def timeseries(db: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    _t = llm_usage_table()
    start = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await db.execute(
        text(
            "SELECT date_trunc('day', ts) AS day, count(*) AS calls, "
            "coalesce(sum(total_tokens),0) AS tokens, coalesce(sum(cost_usd),0) AS cost "
            "FROM llm_usage WHERE ts >= :start GROUP BY 1 ORDER BY 1"
        ),
        {"start": start},
    )
    return [
        {
            "day": str(r[0].date()) if hasattr(r[0], "date") else str(r[0]),
            "calls": int(r[1]),
            "tokens": int(r[2]),
            "cost": round(float(r[3]), 4),
        }
        for r in rows
    ]
