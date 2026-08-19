"""基线：计划书 v1.3 第 5 章的 11 张业务表（P-2 迁移机制引入时冻结的 DDL）。

用途：
- 空库：从 base 升级时创建全部基线表；
- 既有库（create_all 时代创建、无 alembic_version）：由 app.db.migrate_db
  先 stamp 本版本再继续增量升级。

本文件是历史快照，之后改模型必须新增迁移版本，不要回头改这里。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collector_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, comment="enabled / open"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "hot_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, comment="weibo / zhihu / baidu / xhs"),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False, comment="唯一约束（去重键）"),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("fans", sa.Integer(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("collects", sa.Integer(), nullable=False),
        sa.Column("comments", sa.Integer(), nullable=False),
        sa.Column("cluster", sa.String(length=64), nullable=True, comment="聚类标签"),
        sa.Column("raw", sa.JSON(), nullable=True, comment="原始报文"),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    with op.batch_alter_table("hot_items", schema=None) as batch_op:
        batch_op.create_index("ix_hot_items_captured_at", ["captured_at"], unique=False)
        batch_op.create_index("ix_hot_items_cluster", ["cluster"], unique=False)

    op.create_table(
        "pillars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, comment="栏目名，如：本周5个值得装的AI工具"),
        sa.Column("angle", sa.Text(), nullable=False, comment="固定角度/结构说明，进 topic.angle 喂生成"),
        sa.Column("domain", sa.String(length=64), nullable=False, comment="关联领域（标签候选与领域过滤用）"),
        sa.Column("slots_per_week", sa.Integer(), nullable=False, comment="每周期数：1=周更固定档，>1=多期轮换"),
        sa.Column("keywords", sa.JSON(), nullable=True, comment="专属采样关键词池（喂 xhs_sample）"),
        sa.Column("active", sa.Boolean(), nullable=False, comment="停用后不采样不排期，历史选题保留"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False, comment="wechat / xhs"),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False, comment="note / article / teardown"),
        sa.Column("template", sa.Text(), nullable=False, comment="Jinja2 模板全文"),
        sa.Column("variables", sa.JSON(), nullable=True, comment="变量清单"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tag_library",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("heat", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tag_library", schema=None) as batch_op:
        batch_op.create_index("ix_tag_library_domain_heat", ["domain", "heat"], unique=False)
        batch_op.create_index("uq_tag_library_domain_tag", ["domain", "tag"], unique=True)

    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("angle", sa.String(length=255), nullable=False, comment="切入角度"),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, comment="manual / radar"),
        sa.Column("status", sa.String(length=16), nullable=False, comment="new / used / archived"),
        sa.Column("score", sa.Float(), nullable=False, comment="综合评分"),
        sa.Column("evidence", sa.JSON(), nullable=True, comment="雷达证据快照"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="热点保鲜截止，可空；radar 选题 = created_at + 72h"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=16), nullable=False, comment="wechat / xhs"),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, comment="公众号存 Markdown，小红书存正文"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True, comment="平台差异字段 + usage 成本记账"),
        sa.Column("status", sa.String(length=16), nullable=False, comment="ready / failed / published / archived"),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("articles", schema=None) as batch_op:
        batch_op.create_index("ix_articles_platform_status", ["platform", "status"], unique=False)
        batch_op.create_index("ix_articles_topic_id", ["topic_id"], unique=False)

    op.create_table(
        "viral_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hot_item_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("viral_score", sa.Float(), nullable=False, comment="爆文率"),
        sa.Column("title_pattern", sa.String(length=255), nullable=True, comment="标题模式"),
        sa.Column("reason", sa.Text(), nullable=True, comment="LLM 拆解结论"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["hot_item_id"], ["hot_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "week_themes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pillar_id", sa.Integer(), nullable=False, comment="所属栏目"),
        sa.Column("week_start", sa.DateTime(), nullable=False, comment="周一 00:00（周界键）"),
        sa.Column("theme", sa.String(length=128), nullable=False, comment="本周主题，如：AI 视频创作实战周"),
        sa.Column("subtopics", sa.JSON(), nullable=True, comment='[{"title": 子话题, "hot_item_ids": [素材id]}]'),
        sa.Column("status", sa.String(length=16), nullable=False, comment="proposed / confirmed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pillar_id"], ["pillars.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("week_themes", schema=None) as batch_op:
        batch_op.create_index("ix_week_themes_pillar_week", ["pillar_id", "week_start"], unique=True)

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, comment="cover / quote / data"),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "publish_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True, comment="阅读 / 点赞 / 收藏"),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("publish_records")
    op.drop_table("assets")
    with op.batch_alter_table("week_themes", schema=None) as batch_op:
        batch_op.drop_index("ix_week_themes_pillar_week")
    op.drop_table("week_themes")
    op.drop_table("viral_samples")
    with op.batch_alter_table("articles", schema=None) as batch_op:
        batch_op.drop_index("ix_articles_topic_id")
        batch_op.drop_index("ix_articles_platform_status")
    op.drop_table("articles")
    op.drop_table("topics")
    with op.batch_alter_table("tag_library", schema=None) as batch_op:
        batch_op.drop_index("uq_tag_library_domain_tag")
        batch_op.drop_index("ix_tag_library_domain_heat")
    op.drop_table("tag_library")
    op.drop_table("prompts")
    op.drop_table("pillars")
    with op.batch_alter_table("hot_items", schema=None) as batch_op:
        batch_op.drop_index("ix_hot_items_cluster")
        batch_op.drop_index("ix_hot_items_captured_at")
    op.drop_table("hot_items")
    op.drop_table("collector_state")
