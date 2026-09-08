"""M3.1 迁移：用户/AI 生成的因子草稿库（表达式入库，供因子库管理与验证）。"""
from __future__ import annotations

from alembic import op

revision = "0006_user_factors"
down_revision = "0005_llm_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_factors (
            id          UUID PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            expression  TEXT NOT NULL,
            tags        JSONB NOT NULL DEFAULT '["AI"]',
            source_model TEXT,
            status      TEXT NOT NULL DEFAULT 'draft',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_user_factors_created ON user_factors (created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_factors CASCADE;")
