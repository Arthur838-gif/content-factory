"""P-2 基础设施三表：领域词表入库 + 持久化采样任务。

- domains / domain_keywords：领域词表从 data/domains.yml 迁入数据库，
  YAML 降级为种子导入源（幂等导入由 app.services.domain_service.seed_domains
  在启动迁移后执行，不在迁移里做——数据导入可重跑，schema 迁移只管结构）；
- sampling_jobs：采样任务队列（dedupe_key 部分唯一索引只约束活跃任务，
  终态保留键值供审计，同键任务跑完即可再次排队）。

Revision ID: 0002_domains_jobs
Revises: 0001_baseline
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_domains_jobs"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False, comment="custom / official"),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("ordering", sa.Integer(), nullable=False, comment="匹配优先级，小者先"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("domains", schema=None) as batch_op:
        batch_op.create_index("ix_domains_ordering", ["ordering"], unique=False)

    op.create_table(
        "domain_keywords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=64), nullable=False),
        sa.Column("ordering", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("domain_keywords", schema=None) as batch_op:
        batch_op.create_index("ix_domain_keywords_domain_ordering", ["domain_id", "ordering"], unique=False)
        batch_op.create_index("uq_domain_keywords_domain_keyword", ["domain_id", "keyword"], unique=True)

    op.create_table(
        "sampling_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, comment="pillar / manual / scheduled"),
        sa.Column("collector", sa.String(length=32), nullable=False),
        sa.Column("pillar_id", sa.Integer(), nullable=True, comment="历史日志用途，不设外键（栏目删除不挡）"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=True, comment="关键词快照（入队时定死，重试不漂移）"),
        sa.Column("total_queries", sa.Integer(), nullable=False),
        sa.Column("completed_queries", sa.Integer(), nullable=False),
        sa.Column("current_keyword", sa.String(length=64), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("inserted", sa.Integer(), nullable=False),
        sa.Column("filtered_out", sa.Integer(), nullable=False),
        sa.Column("duplicates_skipped", sa.Integer(), nullable=False),
        sa.Column("topics_created", sa.Integer(), nullable=False),
        sa.Column("topics_merged", sa.Integer(), nullable=False),
        sa.Column("viral_created", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True, comment="活跃任务去重键（部分唯一索引约束，终态不挡重跑）"),
        sa.Column("meta", sa.JSON(), nullable=True, comment="扩展信息：降级关键词、各关键词数据源等"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sampling_jobs_pillar_id", "sampling_jobs", ["pillar_id"], unique=False)
    op.create_index("ix_sampling_jobs_status_id", "sampling_jobs", ["status", "id"], unique=False)
    # 部分唯一索引：只约束活跃（queued/running）任务的 dedupe_key，
    # 终态任务保留键值供审计，同键任务跑完即可再次排队
    op.create_index(
        "uq_sampling_jobs_dedupe_active",
        "sampling_jobs",
        ["dedupe_key"],
        unique=True,
        sqlite_where=sa.text("dedupe_key IS NOT NULL AND status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_sampling_jobs_dedupe_active", table_name="sampling_jobs")
    op.drop_index("ix_sampling_jobs_status_id", table_name="sampling_jobs")
    op.drop_index("ix_sampling_jobs_pillar_id", table_name="sampling_jobs")
    op.drop_table("sampling_jobs")
    with op.batch_alter_table("domain_keywords", schema=None) as batch_op:
        batch_op.drop_index("uq_domain_keywords_domain_keyword")
        batch_op.drop_index("ix_domain_keywords_domain_ordering")
    op.drop_table("domain_keywords")
    with op.batch_alter_table("domains", schema=None) as batch_op:
        batch_op.drop_index("ix_domains_ordering")
    op.drop_table("domains")
