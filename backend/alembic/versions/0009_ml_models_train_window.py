"""M3.4 迁移：ml_models 关联训练数据集信息（train_start/train_end/dataset）。

保存模型时记录训练数据的 时间窗 / 用的币种 / 切分信息，供：
- 模型库展示训练数据来源；
- 打分与建策略回测时自动做样本排除（评估起点提到训练窗口之后，避免用样本内数据评估）。
"""
from __future__ import annotations

from alembic import op

revision = "0009_ml_models_train_window"
down_revision = "0008_ml_models_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS train_start TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS train_end   TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS dataset     JSONB NOT NULL DEFAULT '{}';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE ml_models
            DROP COLUMN IF EXISTS train_start,
            DROP COLUMN IF EXISTS train_end,
            DROP COLUMN IF EXISTS dataset;
        """
    )
