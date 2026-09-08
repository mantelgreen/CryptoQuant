"""模拟交易 v0（手动账户：现价市价撮合 + 持仓快照 + 净值曲线）。

撮合规则：按请求时刻的币安最新价成交，taker 费率 0.1%；
只做多（卖出不能超过持仓）；下单即成交并落库、刷新持仓快照与净值曲线。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.models.orm import equity_curves_table, sim_accounts_table, sim_orders_table, sim_positions_table
from ..data.service.market_data import MarketDataService

DEFAULT_CASH = 100_000.0
TAKER_FEE = 0.001


class SimAccountRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = sim_accounts_table()

    async def create(self, name: str, account_type: str = "manual", config: dict[str, Any] | None = None) -> dict[str, Any]:
        aid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=aid, name=name, account_type=account_type,
                config=config or {}, initial_cash=DEFAULT_CASH, current_cash=DEFAULT_CASH,
            )
        )
        await self.db.commit()
        return await self.get(aid)  # type: ignore[return-value]

    async def update_config(self, aid: UUID, config: dict[str, Any]) -> None:
        await self.db.execute(
            self._t.update().where(self._t.c.id == aid).values(config=config)
        )
        await self.db.commit()

    async def get(self, aid: UUID) -> dict[str, Any] | None:
        row = (await self.db.execute(select(self._t).where(self._t.c.id == aid))).first()
        return dict(row._mapping) if row else None

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.db.execute(select(self._t).order_by(desc(self._t.c.created_at)))
        return [dict(r._mapping) for r in rows]

    async def update_cash(self, aid: UUID, cash: float) -> None:
        await self.db.execute(
            self._t.update().where(self._t.c.id == aid).values(
                current_cash=round(cash, 2), updated_at=datetime.now(timezone.utc)
            )
        )
        await self.db.commit()


class SimOrderRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = sim_orders_table()

    async def create_filled(self, account_id: UUID, symbol: str, side: str, qty: float, price: float) -> UUID:
        oid = uuid4()
        now = datetime.now(timezone.utc)
        fee = round(price * qty * TAKER_FEE, 6)
        await self.db.execute(
            self._t.insert().values(
                id=oid, account_id=account_id, symbol=symbol, side=side,
                order_type="market", price=price, qty=qty, leverage=1,
                status="filled", created_at=now, filled_at=now,
                filled_price=price, fee=fee,
            )
        )
        await self.db.commit()
        return oid

    async def by_account(self, account_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            select(self._t)
            .where(self._t.c.account_id == account_id)
            .order_by(desc(self._t.c.created_at))
            .limit(limit)
        )
        return [dict(r._mapping) for r in rows]

    async def all_filled(self, account_id: UUID) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            select(self._t).where(
                and_(self._t.c.account_id == account_id, self._t.c.status == "filled")
            )
        )
        return [dict(r._mapping) for r in rows]

    async def by_symbol(self, symbol: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            select(self._t)
            .where(and_(self._t.c.symbol == symbol.upper(), self._t.c.status == "filled"))
            .order_by(desc(self._t.c.filled_at))
            .limit(limit)
        )
        return [dict(r._mapping) for r in rows]


class SimPositionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = sim_positions_table()

    async def snapshot(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        await self.db.execute(delete(self._t).where(self._t.c.account_id == rows[0]["account_id"]))
        await self.db.execute(self._t.insert().values(rows))
        await self.db.commit()


def _positions_from_orders(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按已成交订单重算持仓：avg_cost 用加权平均成本（卖出不动成本、只减数量）。"""
    pos: dict[str, dict[str, Any]] = {}
    for o in orders:
        sym = o["symbol"]
        qty = float(o["qty"])
        px = float(o["filled_price"] or o["price"])
        p = pos.setdefault(sym, {"qty": 0.0, "avg": 0.0, "cost": 0.0})
        if o["side"] == "buy":
            p["cost"] += qty * px
            p["qty"] += qty
            p["avg"] = p["cost"] / p["qty"] if p["qty"] else 0.0
        else:
            p["qty"] -= qty
            if p["qty"] <= 0:
                p["qty"] = 0.0
                p["avg"] = 0.0
                p["cost"] = 0.0
            else:
                p["avg"] = p["cost"] / p["qty"]
    return {k: v for k, v in pos.items() if v["qty"] > 1e-12}


def _run_type(account: dict[str, Any]) -> str:
    """净值曲线 run_type 与账户类型一致（manual/strategy/llm）。"""
    rt = account.get("account_type") or "manual"
    return rt if rt in ("manual", "strategy", "llm") else "manual"


async def equity_series(db: AsyncSession, run_type: str, account_id: UUID) -> list[dict[str, Any]]:
    _t = equity_curves_table()
    rows = await db.execute(
        select(_t)
        .where(and_(_t.c.run_type == run_type, _t.c.run_id == account_id))
        .order_by(_t.c.ts.asc())
    )
    return [dict(r._mapping) for r in rows]


async def overview(db: AsyncSession) -> list[dict[str, Any]]:
    """板块级绩效汇总：每个账户取最近一笔已记录净值（免实时联网估值）。"""
    accts = await SimAccountRepository(db).list()
    out: list[dict[str, Any]] = []
    for a in accts:
        rt = _run_type(a)
        curve = await equity_series(db, rt, a["id"])
        orders = await SimOrderRepository(db).all_filled(a["id"])
        last = curve[-1] if curve else None
        out.append(
            {
                "id": str(a["id"]),
                "name": a["name"],
                "account_type": a["account_type"],
                "initial_cash": round(float(a["initial_cash"]), 2),
                "current_cash": round(float(a["current_cash"]), 2),
                "last_equity": round(float(last["equity"]), 2) if last else None,
                "orders": len(orders),
                "curve_points": len(curve),
            }
        )
    return out


async def comparison(db: AsyncSession) -> list[dict[str, Any]]:
    """三路对比汇总：按账户类型聚合最近净值曲线（供叠加图）。"""
    accts = await SimAccountRepository(db).list()
    groups: dict[str, list[dict[str, Any]]] = {}
    for a in accts:
        groups.setdefault(_run_type(a), []).append(a)
    out: list[dict[str, Any]] = []
    for rt, members in groups.items():
        curves: list[tuple[datetime, float]] = []
        for a in members:
            for p in await equity_series(db, rt, a["id"]):
                curves.append((p["ts"], float(p["equity"])))
        curves.sort(key=lambda x: x[0])
        out.append(
            {
                "type": rt,
                "label": {"manual": "手动", "strategy": "策略自动", "llm": "LLM"}.get(rt, rt),
                "accounts": [{"id": str(a["id"]), "name": a["name"]} for a in members],
                "latest": round(curves[-1][1], 2) if curves else None,
                "points": [
                    {"ts": ts.isoformat(), "value": round(v, 2)}
                    for ts, v in curves[-600:]
                ],
            }
        )
    return out


async def symbol_activity(db: AsyncSession, symbol: str, limit: int = 30) -> dict[str, Any]:
    """某币在模拟盘的成交/自动交易记录（跨账户），账户类型用于区分手动/策略/LLM。"""
    accts = await SimAccountRepository(db).list()
    by_id = {a["id"]: a for a in accts}
    rows = await SimOrderRepository(db).by_symbol(symbol=symbol, limit=limit)
    items = []
    for r in rows:
        acct = by_id.get(r["account_id"])
        items.append(
            {
                "account_id": str(r["account_id"]),
                "account_name": acct["name"] if acct else "?",
                "account_type": acct["account_type"] if acct else "manual",
                "side": r["side"],
                "qty": float(r["qty"]),
                "price": float(r["filled_price"] or r["price"]),
                "fee": float(r["fee"]),
                "created_at": r["created_at"],
            }
        )
    return {"symbol": symbol.upper(), "items": items}


async def _record_equity(
    db: AsyncSession, run_type: str, account_id: UUID, cash: float, positions: list[dict[str, Any]]
) -> None:
    total = cash + sum(p["value"] for p in positions)
    _t = equity_curves_table()
    await db.execute(
        pg_insert(_t)
        .values(run_type=run_type, run_id=account_id, ts=datetime.now(timezone.utc), equity=round(total, 2))
        .on_conflict_do_nothing(index_elements=["run_type", "run_id", "ts"])
    )
    await db.commit()


async def current_price(symbol: str) -> float:
    svc = MarketDataService()
    data = await svc.list_tickers(symbol=symbol)
    if isinstance(data, list):
        data = data[0] if data else {}
    return float((data or {}).get("lastPrice") or 0)  # type: ignore[index]


async def place_market_order(
    db: AsyncSession,
    account: dict[str, Any],
    symbol: str,
    side: str,
    qty: float,
) -> dict[str, Any]:
    if side not in ("buy", "sell") or qty <= 0:
        raise ValueError("invalid_side_or_qty")
    price = await current_price(symbol)
    if price <= 0:
        raise ValueError("no_price_for_symbol")

    orders = await SimOrderRepository(db).all_filled(account["id"])
    positions = _positions_from_orders(orders)
    cash = float(account["current_cash"])

    notional = price * qty
    fee = notional * TAKER_FEE
    if side == "buy":
        cost = notional + fee
        if cost > cash + 1e-6:
            raise ValueError("insufficient_cash")
        cash -= cost
    else:
        if qty > positions.get(symbol, {}).get("qty", 0.0) + 1e-9:
            raise ValueError("insufficient_position")
        cash += notional - fee

    oid = await SimOrderRepository(db).create_filled(account["id"], symbol, side, qty, price)
    # 重算持仓快照（含刚成交）
    orders = await SimOrderRepository(db).all_filled(account["id"])
    pos = _positions_from_orders(orders)
    snap_rows = []
    total_val = 0.0
    valued: list[dict[str, Any]] = []
    for sym, p in pos.items():
        mark = price if sym == symbol else await current_price(sym)
        mv = p["qty"] * mark
        total_val += mv
        snap_rows.append(
            {
                "account_id": account["id"], "symbol": sym, "qty": p["qty"],
                "avg_price": round(p["avg"], 8), "mark_price": mark,
                "unrealized_pnl": round((mark - p["avg"]) * p["qty"], 2),
                "ts": datetime.now(timezone.utc),
            }
        )
        valued.append({"symbol": sym, "qty": p["qty"], "avg": round(p["avg"], 8), "mark": mark, "value": round(mv, 2)})

    await SimPositionRepository(db).snapshot(snap_rows)
    await SimAccountRepository(db).update_cash(account["id"], cash)
    equity = cash + total_val
    await _record_equity(db, _run_type(account), account["id"], cash, valued)
    return {
        "order_id": str(oid), "symbol": symbol, "side": side, "qty": qty,
        "fill_price": round(price, 8), "fee": round(fee, 6),
        "cash": round(cash, 2), "equity": round(equity, 2),
    }


async def account_detail(db: AsyncSession, account: dict[str, Any]) -> dict[str, Any]:
    orders = await SimOrderRepository(db).all_filled(account["id"])
    pos = _positions_from_orders(orders)
    cash = float(account["current_cash"])
    valued: list[dict[str, Any]] = []
    equity = cash
    for sym, p in pos.items():
        mark = await current_price(sym)
        if mark <= 0:
            continue
        mv = p["qty"] * mark
        equity += mv
        valued.append(
            {
                "symbol": sym, "qty": p["qty"], "avg": round(p["avg"], 8),
                "mark": mark, "value": round(mv, 2),
                "unrealized_pnl": round((mark - p["avg"]) * p["qty"], 2),
            }
        )
    recent = await SimOrderRepository(db).by_account(account["id"], 200)
    curve = await equity_series(db, _run_type(account), account["id"])
    return {
        "account": {**account, "current_cash": round(cash, 2)},
        "positions": valued,
        "orders": recent,
        "equity": curve,
        "total_equity": round(equity, 2),
    }

async def run_auto_tick(db: AsyncSession, account: dict[str, Any]) -> dict[str, Any]:
    """策略自动账户：用保存的 ML 模型对最新 K 线打分，触发开/平仓（模拟盘，纸面）。"""
    from uuid import UUID as _UUID

    from ..data.repository.kline_repo import KlineRepository
    from ..factor.repo import MlModelRepository
    from ..factor.ml import score_with_model

    cfg = account.get("config") or {}
    symbol = (cfg.get("symbol") or "").upper()
    interval = cfg.get("interval") or ""
    model_id = cfg.get("model_id")
    if not symbol or not interval or not model_id:
        return {"action": "error", "message": "账户缺少 symbol/interval/model_id 配置"}

    model = await MlModelRepository(db).get(_UUID(model_id))
    if not model or not model.get("features"):
        return {"action": "error", "message": "模型不存在或特征缺失"}

    end = datetime.now(timezone.utc)
    days = float(cfg.get("lookback") or 0)
    if not days:
        days = 400 if interval.endswith("d") else 40 if interval.endswith("h") else 7
    start = end - timedelta(days=days)
    scored = await score_with_model(
        KlineRepository(db), model["features"], interval, symbol, start, end
    )
    last = scored.get("last_score")
    if last is None:
        return {"action": "skip", "message": scored.get("message", "无可用打分")}

    orders = await SimOrderRepository(db).all_filled(account["id"])
    pos = _positions_from_orders(orders).get(symbol, {})
    qty = float(pos.get("qty", 0.0))
    cash = float(account["current_cash"])
    enter = float(cfg.get("enter", 0.02) or 0.02)
    exit_ = float(cfg.get("exit", -0.02) or -0.02)

    if qty <= 1e-9 and last > enter:
        price = await current_price(symbol)
        if price <= 0:
            return {"action": "skip", "message": "无法取到现价"}
        buy_qty = (cash * 0.95) / price
        result = await place_market_order(db, account, symbol, "buy", buy_qty)
        return {"action": "open", "score": round(last, 6), "order": result}
    if qty > 1e-9 and last < exit_:
        result = await place_market_order(db, account, symbol, "sell", qty)
        return {"action": "close", "score": round(last, 6), "order": result}
    return {"action": "hold", "score": round(last, 6), "qty": round(qty, 6)}
