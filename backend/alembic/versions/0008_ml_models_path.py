"""M3.3 迁移：ml_models 增加 model_path，指向磁盘上的 pickle artifact。

Ridge 只存线性权重即可复用；LightGBM 等树模型需序列化到磁盘，
DB 通过相对文件名（<id>.pkl，见 factor.ml.model_storage_dir）引用。
"""
from __future__ import annotations

from alembic import op

revision = "0008_ml_models_path"
down_revision = "0007_llm_providers_thinking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS model_path TEXT;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ml_models DROP COLUMN IF EXISTS model_path;")
