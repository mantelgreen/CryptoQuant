"""M2.2 迁移：ML 模型 artifact 表（保存训练好的 Ridge 模型供复用打分）。"""
from __future__ import annotations

from alembic import op

revision = "0004_ml_models"
down_revision = "0003_factor_ic_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_models (
            id          UUID PRIMARY KEY,
            name        TEXT NOT NULL,
            model_type  TEXT NOT NULL DEFAULT 'ridge',
            interval    TEXT NOT NULL,
            horizon     INTEGER NOT NULL,
            symbols     JSONB NOT NULL DEFAULT '[]',
            features    JSONB NOT NULL DEFAULT '[]',
            metrics     JSONB NOT NULL DEFAULT '{}',
            n_train     INTEGER NOT NULL DEFAULT 0,
            n_test      INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_ml_models_created ON ml_models (created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ml_models CASCADE;")
