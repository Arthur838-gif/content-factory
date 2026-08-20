"""模型配置表：文案/图片大模型运行时可切换（/models 页）。

- model_configs：多组 (名称, base_url, api_key, 模型名, 单价, thinking 开关)；
  purpose（text/image）各至多一条 is_active（SQLite 部分唯一索引约束），
  无 active 行时调用方回退 .env 的 OPENAI_*（兼容存量部署，迁移只管结构）。

Revision ID: 0003_model_configs
Revises: 0002_domains_jobs
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_model_configs"
down_revision: Union[str, None] = "0002_domains_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False, comment="text=文案 / image=图片"),
        sa.Column("name", sa.String(length=64), nullable=False, comment="显示名，如 GLM-4.7 文案主力"),
        sa.Column("base_url", sa.String(length=256), nullable=False),
        sa.Column("api_key", sa.String(length=256), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False, comment="调用时的 model 参数"),
        sa.Column("price_input_per_m", sa.Float(), nullable=True, comment="元/百万输入 token；空=回退 env 默认"),
        sa.Column("price_output_per_m", sa.Float(), nullable=True, comment="元/百万输出 token；空=回退 env 默认"),
        sa.Column("disable_thinking", sa.String(length=8), nullable=True, comment="空=按模型名自动（glm 前缀）/ on / off"),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="每用途至多一条"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # 部分唯一索引：每用途至多一条当前使用；备用行互不干扰
    op.create_index(
        "uq_model_configs_active_purpose",
        "model_configs",
        ["purpose"],
        unique=True,
        sqlite_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_model_configs_active_purpose", table_name="model_configs")
    op.drop_table("model_configs")
