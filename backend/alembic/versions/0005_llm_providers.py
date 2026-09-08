"""M3.0 迁移：LLM 模型/供应商设置表（base_url、api_key、模型参数）。"""
from __future__ import annotations

from alembic import op

revision = "0005_llm_providers"
down_revision = "0004_ml_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_providers (
            id          UUID PRIMARY KEY,
            name        TEXT NOT NULL,
            provider    TEXT NOT NULL DEFAULT 'openai',
            base_url    TEXT,
            api_key     TEXT,
            model       TEXT NOT NULL DEFAULT '',
            temperature REAL NOT NULL DEFAULT 0.2,
            max_tokens  INTEGER NOT NULL DEFAULT 2000,
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            is_default  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (name)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_providers CASCADE;")
