"""M3.2 迁移：llm_providers 增加 disable_thinking（关闭思维/推理模式）开关。

思考型模型（DeepSeek v4 / Kimi K2-Thinking / Qwen3 / R1 等）会把推理计入
max_tokens，导致 Agent 拿不到最终 JSON。勾选后由 llm 客户端按供应商注入对应参数。
"""
from __future__ import annotations

from alembic import op

revision = "0007_llm_providers_thinking"
down_revision = "0006_user_factors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_providers
            ADD COLUMN IF NOT EXISTS disable_thinking BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE llm_providers DROP COLUMN IF EXISTS disable_thinking;")
