"""第 5 章数据模型合同（P-1a 建表）。

唯一事实来源：《双端内容工厂 · 开发计划书 v1.3》第 5 章。
字段名、类型、约束与文档逐一对应；新增字段前先更新文档并提升版本号。

时间约定：全部使用本地时区 naive datetime，与 APScheduler 本地调度一致。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now()


class Topic(Base):
    """topics：选题，不带平台属性（platform 在生成时才绑定）。"""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    angle: Mapped[str] = mapped_column(String(255), default="", comment="切入角度")
    domain: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual", comment="manual / radar")
    status: Mapped[str] = mapped_column(String(16), default="new", comment="new / used / archived")
    score: Mapped[float] = mapped_column(Float, default=0.0, comment="综合评分")
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="雷达证据快照")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="热点保鲜截止，可空；radar 选题 = created_at + 72h"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Prompt(Base):
    """prompts：模板即数据；同 platform + scenario 取 enabled 中最高 version。"""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), comment="wechat / xhs")
    name: Mapped[str] = mapped_column(String(128))
    scenario: Mapped[str] = mapped_column(String(32), comment="note / article / teardown")
    template: Mapped[str] = mapped_column(Text, comment="Jinja2 模板全文")
    variables: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="变量清单")
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Article(Base):
    """articles：一个 topic 对应 N 条（每平台一条）；平台差异一律进 meta。"""

    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_platform_status", "platform", "status"),
        Index("ix_articles_topic_id", "topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), comment="wechat / xhs")
    title: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="", comment="公众号存 Markdown，小红书存正文")
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="平台差异字段 + usage 成本记账")
    status: Mapped[str] = mapped_column(String(16), comment="ready / failed / published / archived")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Asset(Base):
    """assets：生成产物登记，path 相对 data/。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), comment="cover / quote / data")
    path: Mapped[str] = mapped_column(String(255))
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HotItem(Base):
    """hot_items：热榜与样本统一结构；url 为去重键（无 URL 用 sha1 填充）。"""

    __tablename__ = "hot_items"
    __table_args__ = (
        Index("ix_hot_items_captured_at", "captured_at"),
        Index("ix_hot_items_cluster", "cluster"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), comment="weibo / zhihu / baidu / xhs")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(512), unique=True, comment="唯一约束（去重键）")
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fans: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    collects: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    cluster: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="聚类标签")
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="原始报文")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ViralSample(Base):
    """viral_samples：低粉爆款判定结果（P-1b 落数据，表结构 P-1a 先建）。"""

    __tablename__ = "viral_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hot_item_id: Mapped[int] = mapped_column(ForeignKey("hot_items.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), default="")
    viral_score: Mapped[float] = mapped_column(Float, default=0.0, comment="爆文率")
    title_pattern: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="标题模式")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="LLM 拆解结论")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TagLibrary(Base):
    """tag_library：domain + tag 唯一，heat 为出现频次累计。"""

    __tablename__ = "tag_library"
    __table_args__ = (
        Index("uq_tag_library_domain_tag", "domain", "tag", unique=True),
        Index("ix_tag_library_domain_heat", "domain", "heat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(64))
    tag: Mapped[str] = mapped_column(String(64))
    heat: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PublishRecord(Base):
    """publish_records：只增不改；article_id 指向发布时那一行，不漂移。"""

    __tablename__ = "publish_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(16))
    account: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="阅读 / 点赞 / 收藏")
    published_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


ALL_MODELS = (
    Topic,
    Prompt,
    Article,
    Asset,
    HotItem,
    ViralSample,
    TagLibrary,
    PublishRecord,
)
