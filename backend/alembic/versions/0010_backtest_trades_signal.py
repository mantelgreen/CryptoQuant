"""M4 迁移：backtest_trades 增加 signal / position 列，用于“回测日志”展示触发信号与仓位。"""
from __future__ import annotations

from alembic import op

revision = "0010_backtest_trades_signal"
down_revision = "0009_ml_models_train_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_trades
            ADD COLUMN IF NOT EXISTS signal     DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS position   DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS notional   DOUBLE PRECISION;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_trades
            DROP COLUMN IF EXISTS signal,
            DROP COLUMN IF EXISTS position,
            DROP COLUMN IF EXISTS notional;
        """
    )
