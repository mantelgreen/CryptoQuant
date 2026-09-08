"""M2.1 迁移：因子 IC 研究结果表（供研究记录回看/筛选跟踪）。"""
from __future__ import annotations

from alembic import op

revision = "0003_factor_ic_runs"
down_revision = "0002_m2_caggs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_ic_runs (
            id          UUID PRIMARY KEY,
            code        TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            interval    TEXT NOT NULL,
            start_time  TIMESTAMPTZ NOT NULL,
            end_time    TIMESTAMPTZ NOT NULL,
            ic          DOUBLE PRECISION,
            rank_ic     DOUBLE PRECISION,
            ic_h1       DOUBLE PRECISION,
            ic_h3       DOUBLE PRECISION,
            ic_h5       DOUBLE PRECISION,
            n           INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_factor_ic_runs_code_ts
            ON factor_ic_runs (code, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS factor_ic_runs CASCADE;")
