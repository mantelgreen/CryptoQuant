"""M1.5 迁移：从 1m K 线创建 5m/15m/1h/1d 连续聚合物化视图（Continuous Aggregates）。

TimescaleDB 约束：聚合窗口必须是基线 interval 的整数倍。
基线 1m → 5m/15m/1h/1d 均满足（C10 修正口径）。2h/8h/12h/3d/1w/1M 等
不可建 CA，查询时用 time_bucket 现算。
"""
from __future__ import annotations

from alembic import op

revision = "0002_m2_caggs"
down_revision = "0001_m1_initial"
branch_labels = None
depends_on = None


def _create_cagg(
    view: str,
    bucket: str,
    bucket_col: str,
    schedule: str,
    start_offset: str,
    end_offset: str,
) -> None:
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
        WITH (timescaledb.continuous) AS
        SELECT symbol,
               time_bucket('{bucket}', open_time) AS {bucket_col},
               first(open, open_time)  AS open,
               max(high)               AS high,
               min(low)                AS low,
               last(close, open_time)  AS close,
               sum(volume)             AS volume,
               sum(quote_volume)       AS quote_volume
        FROM klines
        WHERE interval = '1m'
        GROUP BY symbol, {bucket_col}
        WITH NO DATA;
        """
    )
    op.execute(
        f"""
        SELECT add_continuous_aggregate_policy(
            '{view}',
            start_offset      => INTERVAL '{start_offset}',
            end_offset        => INTERVAL '{end_offset}',
            schedule_interval => INTERVAL '{schedule}'
        );
        """
    )


def _drop_cagg(view: str) -> None:
    op.execute(
        f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => TRUE);"
    )
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE;")


def upgrade() -> None:
    _create_cagg("klines_5m", "5 minutes", "bucket", "5 minutes", "1 day", "10 minutes")
    _create_cagg("klines_15m", "15 minutes", "bucket", "15 minutes", "1 day", "15 minutes")
    _create_cagg("klines_1h", "1 hour", "bucket", "1 hour", "3 days", "1 hour")
    _create_cagg("klines_1d", "1 day", "bucket", "1 day", "5 days", "1 day")


def downgrade() -> None:
    _drop_cagg("klines_1d")
    _drop_cagg("klines_1h")
    _drop_cagg("klines_15m")
    _drop_cagg("klines_5m")
