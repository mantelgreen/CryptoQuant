"""M6 迁移：AI 流水线（pipeline_runs + pipeline_rounds）。

研究板块 AI 流水线：因子生成→选择→训练→回测→评估→（入库|诊断）多轮自进化闭环。
一轮一个 ARQ job，进度经 Redis ws:pipeline:{id} 推送。
"""
from __future__ import annotations

from alembic import op

revision = "0012_pipeline"
down_revision = "0011_backtest_trades_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id            UUID PRIMARY KEY,
            name          TEXT NOT NULL,
            config        JSONB NOT NULL DEFAULT '{}',
            status        TEXT NOT NULL DEFAULT 'pending',
            current_round INTEGER NOT NULL DEFAULT 0,
            factor_pool   JSONB NOT NULL DEFAULT '[]',
            stall_count   INTEGER NOT NULL DEFAULT 0,
            best_model_id UUID,
            best_metrics  JSONB,
            error         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at    TIMESTAMPTZ,
            finished_at   TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_pipeline_runs_created ON pipeline_runs (created_at DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_rounds (
            id               UUID PRIMARY KEY,
            pipeline_run_id  UUID NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
            round_no         INTEGER NOT NULL,
            phase            TEXT NOT NULL DEFAULT 'gen',
            status           TEXT NOT NULL DEFAULT 'pending',
            factors_generated JSONB,
            factors_picked    JSONB,
            train_result      JSONB,
            backtest_summary  JSONB,
            diagnosis         JSONB,
            model_id          UUID,
            error             TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (pipeline_run_id, round_no)
        );
        CREATE INDEX IF NOT EXISTS ix_pipeline_rounds_run ON pipeline_rounds (pipeline_run_id, round_no);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pipeline_rounds CASCADE;")
    op.execute("DROP TABLE IF EXISTS pipeline_runs CASCADE;")
