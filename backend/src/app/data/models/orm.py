"""SQLAlchemy ORM 模型（M1）。

设计原则：
- 启动时通过 sync engine 反射表结构到 SQLAlchemy metadata。
- 业务方法（bulk_insert / query）走 async session。
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy import types as sa_types
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID

from ...core.config import get_settings
from ...core.db import Base, engine
from sqlalchemy import inspect

# 注册 pgvector 的 vector 类型，避免反射带 embedding 列时告警
from sqlalchemy.dialects.postgresql.base import ischema_names


class _VectorType(sa_types.UserDefinedType):
    """只读映射 pgvector `vector` 列（本项目不写向量）。"""

    cache_ok = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        # 反射 `vector(1024)` 时会把维度作为参数传入，这里吸收掉即可
        super().__init__()

    def get_col_spec(self, **kw: object) -> str:  # noqa: ARG002
        return "VECTOR"

    def bind_processor(self, dialect):  # noqa: ARG002
        return None

    def result_processor(self, dialect, coltype):  # noqa: ARG002
        return None


if "vector" not in ischema_names:
    ischema_names["vector"] = _VectorType

settings = get_settings()

# 用独立 MetaData 反射（不绑定 Base.metadata，避免 alembic 与反射互相干扰）
_metadata: MetaData = MetaData()


def _reflect_all() -> None:
    """通过同步连接反射表结构。"""
    from sqlalchemy import create_engine as _ce

    sync_engine = _ce(settings.DATABASE_URL_SYNC)
    try:
        insp = inspect(sync_engine)
        names = [
            "symbols", "klines", "tickers_24h",
            "download_jobs", "scheduled_jobs",
            "strategies", "strategy_versions",
            "backtests", "backtest_trades", "equity_curves",
            "factor_library", "factor_versions", "factor_values",
            "factor_research_runs", "factor_ic_runs", "ml_models",
            "agent_sessions", "agent_messages", "agent_decisions",
            "sim_accounts", "sim_orders", "sim_positions",
            "llm_usage", "llm_costs", "llm_providers", "user_factors", "audit_logs",
        ]
        for n in names:
            if n in insp.get_table_names():
                Table(n, _metadata, autoload_with=sync_engine, extend_existing=True)
    finally:
        sync_engine.dispose()


_reflect_all()


def _t(name: str) -> Table:
    return _metadata.tables[name]


# 反射结果表对象（按需 import 使用）
def symbols_table() -> Table:
    return _t("symbols")


def klines_table() -> Table:
    return _t("klines")


def tickers_24h_table() -> Table:
    return _t("tickers_24h")


def download_jobs_table() -> Table:
    return _t("download_jobs")


def scheduled_jobs_table() -> Table:
    return _t("scheduled_jobs")


def llm_usage_table() -> Table:
    return _t("llm_usage")


def llm_costs_table() -> Table:
    return _t("llm_costs")


def audit_logs_table() -> Table:
    return _t("audit_logs")


def strategies_table() -> Table:
    return _t("strategies")


def strategy_versions_table() -> Table:
    return _t("strategy_versions")


def backtests_table() -> Table:
    return _t("backtests")


def backtest_trades_table() -> Table:
    return _t("backtest_trades")


def equity_curves_table() -> Table:
    return _t("equity_curves")


def factor_ic_runs_table() -> Table:
    return _t("factor_ic_runs")


def ml_models_table() -> Table:
    return _t("ml_models")


def sim_accounts_table() -> Table:
    return _t("sim_accounts")


def sim_orders_table() -> Table:
    return _t("sim_orders")


def sim_positions_table() -> Table:
    return _t("sim_positions")


def llm_providers_table() -> Table:
    return _t("llm_providers")


def user_factors_table() -> Table:
    return _t("user_factors")


__all__ = [
    "symbols_table",
    "klines_table",
    "tickers_24h_table",
    "download_jobs_table",
    "scheduled_jobs_table",
    "strategies_table",
    "strategy_versions_table",
    "backtests_table",
    "backtest_trades_table",
    "equity_curves_table",
    "factor_ic_runs_table",
    "ml_models_table",
    "sim_accounts_table",
    "sim_orders_table",
    "sim_positions_table",
    "llm_providers_table",
    "user_factors_table",
    "llm_usage_table",
    "llm_costs_table",
    "audit_logs_table",
    # 工具
    "select",
    "Column",
    "JSONB",
    "PgUUID",
    "ARRAY",
    "String",
    "Text",
    "Integer",
    "BigInteger",
    "Float",
    "Numeric",
    "Boolean",
    "DateTime",
    "func",
    "JSON",
]
