"""第 5 章数据模型合同（P-1a 建表）。

唯一事实来源：《双端内容工厂 · 开发计划书 v1.3》第 5 章。
字段名、类型、约束与文档逐一对应；新增字段前先更新文档并提升版本号。
例外：P-2 基础设施三表（Domain / DomainKeyword / SamplingJob）不在计划书
第 5 章内，是词表入库 + 任务持久化改造的新增合同，变更同样走 Alembic 迁移。

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
    text,
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


class Pillar(Base):
    """pillars：内容栏目（可持续系列，P5）。

    一个栏目 = 固定名称 + 固定角度结构 + 每周期数 + 专属采样关键词池；
    排期（services.pillar.plan_week）按周期从当周采样素材派生选题
    （topics.source='pillar'，不参与 radar 撞题合并）。
    """

    __tablename__ = "pillars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), comment="栏目名，如：本周5个值得装的AI工具")
    angle: Mapped[str] = mapped_column(Text, default="", comment="固定角度/结构说明，进 topic.angle 喂生成")
    domain: Mapped[str] = mapped_column(String(64), default="", comment="关联领域（标签候选与领域过滤用）")
    slots_per_week: Mapped[int] = mapped_column(Integer, default=1, comment="每周期数：1=周更固定档，>1=多期轮换")
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="专属采样关键词池（喂 xhs_sample）")
    active: Mapped[bool] = mapped_column(Boolean, default=True, comment="停用后不采样不排期，历史选题保留")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WeekTheme(Base):
    """week_themes：栏目周主题（P5b，一个栏目每周一条）。

    深挖栏目每周先定主题再分期：LLM 从当周采样素材归纳 theme 并拆出
    subtopics（互补子话题，各成一期深挖），排期按子话题建题并带期数，
    解决"每期各写各的、系列无联动"的问题。
    status：proposed（建议，待确认）/ confirmed（已确认，排期采用）。
    """

    __tablename__ = "week_themes"
    __table_args__ = (Index("ix_week_themes_pillar_week", "pillar_id", "week_start", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pillar_id: Mapped[int] = mapped_column(Integer, ForeignKey("pillars.id"), comment="所属栏目")
    week_start: Mapped[datetime] = mapped_column(DateTime, comment="周一 00:00（周界键）")
    theme: Mapped[str] = mapped_column(String(128), default="", comment="本周主题，如：AI 视频创作实战周")
    subtopics: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment='[{"title": 子话题, "hot_item_ids": [素材id]}]'
    )
    status: Mapped[str] = mapped_column(String(16), default="proposed", comment="proposed / confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CollectorState(Base):
    """collector_state：采集器熔断状态（P-1b）。

    连续失败达到阈值置 status=open（熔断），仅人工恢复（resume）清零；
    不自动重试不自愈。
    """

    __tablename__ = "collector_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="enabled", comment="enabled / open")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Domain(Base):
    """domains：领域词表（P-2 起数据库为唯一事实源，domains.yml 只作种子导入源）。

    ordering 编码匹配优先级（多领域命中取先声明者）：YAML 种子按声明顺序
    分配，官方类目垫后，新建领域追加在尾部。停用（enabled=False）后不参与
    匹配与采样，但保留历史数据引用（topics.domain 等存字符串快照，无外键）。
    """

    __tablename__ = "domains"
    __table_args__ = (Index("ix_domains_ordering", "ordering"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16), default="custom", comment="custom / official")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ordering: Mapped[int] = mapped_column(Integer, default=0, comment="匹配优先级，小者先")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DomainKeyword(Base):
    """domain_keywords：领域关键词（领域内唯一；来源 seed=种子导入 / user=建栏目等人工 / discovery=推荐词）。"""

    __tablename__ = "domain_keywords"
    __table_args__ = (
        Index("uq_domain_keywords_domain_keyword", "domain_id", "keyword", unique=True),
        Index("ix_domain_keywords_domain_ordering", "domain_id", "ordering"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(64))
    ordering: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SamplingJob(Base):
    """sampling_jobs：采样任务（P-2 持久化队列 + 可恢复 worker）。

    API 只入队（202 + job_id），worker 原子 claim + lease/heartbeat 逐关键词执行。
    status 终态：succeeded / succeeded_empty / failed / blocked / canceled；
    succeeded_empty = 全关键词跑完但零抓取（合法结果，不触发熔断）。
    dedupe_key 常驻保留（历史可审计）；唯一性只约束活跃任务
    （SQLite 部分唯一索引），同一键的任务跑完即可再次排队。
    """

    __tablename__ = "sampling_jobs"
    __table_args__ = (
        Index("ix_sampling_jobs_status_id", "status", "id"),
        Index("ix_sampling_jobs_pillar_id", "pillar_id"),
        Index(
            "uq_sampling_jobs_dedupe_active",
            "dedupe_key",
            unique=True,
            sqlite_where=text(
                "dedupe_key IS NOT NULL AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="manual", comment="pillar / manual / scheduled")
    collector: Mapped[str] = mapped_column(String(32), default="xhs_sample")
    pillar_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="历史日志用途，不设外键（栏目删除不挡）")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="关键词快照（入队时定死，重试不漂移）")
    total_queries: Mapped[int] = mapped_column(Integer, default=0)
    completed_queries: Mapped[int] = mapped_column(Integer, default=0)
    current_keyword: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    filtered_out: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)
    topics_created: Mapped[int] = mapped_column(Integer, default=0)
    topics_merged: Mapped[int] = mapped_column(Integer, default=0)
    viral_created: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="活跃任务去重键（部分唯一索引约束，终态不挡重跑）"
    )
    meta: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="扩展信息：降级关键词、各关键词数据源等"
    )


class ModelConfig(Base):
    """model_configs：大模型配置（文案/图片各自的「当前使用」运行时可切换）。

    页面（/models）维护多组供应商配置，每个 purpose（text/image）至多一条
    is_active；无 active 行时调用方回退 .env 的 OPENAI_*（兼容存量部署）。
    api_key 明文存本地库（data/ 已 gitignore 的单机工具），API/页面只回掩码。
    """

    __tablename__ = "model_configs"
    __table_args__ = (
        Index(
            "uq_model_configs_active_purpose",
            "purpose",
            unique=True,
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16), comment="text=文案 / image=图片")
    name: Mapped[str] = mapped_column(String(64), unique=True, comment="显示名，如 GLM-4.7 文案主力")
    base_url: Mapped[str] = mapped_column(String(256))
    api_key: Mapped[str] = mapped_column(String(256))
    model: Mapped[str] = mapped_column(String(128), comment="调用时的 model 参数")
    price_input_per_m: Mapped[float | None] = mapped_column(
        nullable=True, comment="元/百万输入 token；空=回退 env 默认"
    )
    price_output_per_m: Mapped[float | None] = mapped_column(
        nullable=True, comment="元/百万输出 token；空=回退 env 默认"
    )
    disable_thinking: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="空=按模型名自动（glm 前缀）/ on / off"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, comment="每用途至多一条")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


ALL_MODELS = (
    Topic,
    Prompt,
    Article,
    Asset,
    HotItem,
    ViralSample,
    TagLibrary,
    PublishRecord,
    CollectorState,
    Pillar,
    WeekTheme,
    Domain,
    DomainKeyword,
    SamplingJob,
    ModelConfig,
)
