"""M5 迁移：backtest_trades 增加 basis 列（该笔成交的买入均价 / 持仓成本基准）。"""
from __future__ import annotations

from alembic import op

revision = "0011_backtest_trades_basis"
down_revision = "0010_backtest_trades_signal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_trades
            ADD COLUMN IF NOT EXISTS basis DOUBLE PRECISION;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_trades
            DROP COLUMN IF EXISTS basis;
        """
    )
