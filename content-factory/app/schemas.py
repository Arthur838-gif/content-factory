"""Pydantic 模型。HotItem 与采集结果（P-1a）、WechatArticle（P0）、XhsNote（P1）。"""
import hashlib
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class HotItem(BaseModel):
    """采集器统一结构（计划书 6.2：fetch() -> list[HotItem]）。

    url 为去重键；无 URL 的源按第 5 章约定用 sha1(source + title + author) 填充。
    """

    source: str  # weibo / zhihu / baidu / xhs
    title: str
    url: str = ""
    author: str | None = None
    fans: int = 0
    likes: int = 0
    collects: int = 0
    comments: int = 0
    captured_at: datetime | None = None  # 空 → 落库时取当前时间
    raw: dict | None = None  # 原始报文快照

    @model_validator(mode="after")
    def _fill_url(self) -> "HotItem":
        if not self.url:
            digest = hashlib.sha1(
                f"{self.source}{self.title}{self.author or ''}".encode("utf-8")
            ).hexdigest()
            object.__setattr__(self, "url", digest)
        return self


class CollectorRunResult(BaseModel):
    """POST /api/collectors/{name}/run 的返回（含"本次入库条数"）。"""

    collector: str
    fetched: int
    duplicates_skipped: int
    filtered_out: int
    inserted: int  # 本次入库条数
    topics_created: int
    topics_merged: int
    viral_created: int = 0  # 本次判定为低粉爆款并落 viral_samples 的条数（P-1b）


class WechatArticle(BaseModel):
    """公众号输出 Schema（计划书 6.1 / SDD 5.6，M5 与适配层之间的契约）。

    LLM 只产出这三个字段的结构化 JSON；HTML 渲染与草稿箱推送属 M6，P0 不做。
    """

    title: str = Field(..., description="信息密度高的标题，≤ 30 字")
    digest: str = Field(..., description="摘要，≤ 54 字")
    content_md: str = Field(..., description="Markdown 正文，1500-3000 字，含小标题分级")


class XhsNote(BaseModel):
    """小红书输出 Schema（计划书 6.1 / SDD 5.6，M5 与适配层之间的契约）。

    LLM 只产出这五个字段的结构化 JSON；#标签拼接与图文合成（P2）属 M7 适配层。
    title/content 带平台硬上限校验（超限直接生成失败进重试，模型按报错自纠），
    免得用户发布前手动剪文案；cover_text/金句超长由 imaging 自动缩字号兜底，保持软约束。
    """

    title: str = Field(..., min_length=1, description="标题，≤ 20 字，含 1-2 个 emoji 与情绪词或数字")
    content: str = Field(..., min_length=1, description="口语化正文，每段 ≤ 3 行，段间空一行，总字数 300-800")
    tags: list[str] = Field(..., description="3-5 个相关标签，不带 # 号")
    cover_text: str = Field(..., description="封面主标题文案，≤ 12 字，有冲击力")
    image_quotes: list[str] = Field(..., description="2-4 句金句，每句 ≤ 20 字，可直接印在图上")

    @field_validator("title")
    @classmethod
    def _title_platform_limit(cls, value: str) -> str:
        # 小红书发布标题上限 20 字，超了用户得手动剪——在生成侧拦下让模型重出
        if len(value) > 20:
            raise ValueError(f"标题 {len(value)} 字超过小红书 20 字上限，请压缩：「{value}」")
        return value

    @field_validator("content")
    @classmethod
    def _content_platform_limit(cls, value: str) -> str:
        # 正文 + 末尾标签行合计 ≤ 1000（小红书正文上限）；给标签行留 50 字余量
        if len(value) > 950:
            raise ValueError(
                f"正文 {len(value)} 字过长：加上标签行将超小红书 1000 字上限，请压缩到 950 字内"
            )
        return value


class ManualSampleInput(BaseModel):
    """POST /api/viral-samples/manual 人工喂样本（P-1b 降级模式入口）。

    fans 必须显式填写（自动采样拿不到 fans 时，低粉爆款只能经此入口补齐）；
    录入后与自动样本走完全相同的打分、落库、撞题与建题管线。
    """

    url: str = Field(..., max_length=1000, description="笔记链接，http(s) 开头")
    title: str = Field(..., min_length=1, max_length=200, description="笔记标题")
    author: str = Field(..., min_length=1, max_length=100, description="作者昵称")
    # 上限 10^9：拦脏数据/溢出值，正常互动量纲远够用
    fans: int = Field(..., ge=0, le=10**9, description="粉丝数")
    likes: int = Field(..., ge=0, le=10**9, description="点赞数")
    collects: int = Field(..., ge=0, le=10**9, description="收藏数")
    comments: int = Field(..., ge=0, le=10**9, description="评论数")
    domain: str = Field(..., max_length=50, description="领域，须为 data/domains.yml 中的领域名")

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return value


class TeardownTag(BaseModel):
    """周度拆解产出的建议标签（写回 tag_library 累计 heat）。"""

    domain: str
    tag: str


class TeardownSample(BaseModel):
    """周度拆解对单条样本的结论（写回 viral_samples.reason / title_pattern）。"""

    hot_item_id: int
    title_pattern: str = ""
    reason: str = ""


class ViralTeardown(BaseModel):
    """A3 周度拆解输出 Schema（P-1b，M5 与拆解编排之间的契约）。

    LLM 只产出这个结构化 JSON；samples 缺省时按聚合结论回写全部当周样本。
    """

    title_patterns: list[str] = Field(default_factory=list, description="3-5 条可复制的标题模式")
    emotion_words: list[str] = Field(default_factory=list, description="高频情绪词")
    structures: list[str] = Field(default_factory=list, description="结构套路")
    tags: list[TeardownTag] = Field(default_factory=list, description="建议沉淀的标签")
    samples: list[TeardownSample] = Field(default_factory=list, description="逐样本结论")


class SubtopicPlan(BaseModel):
    """周主题规划对单个子话题（深挖一期）的安排。"""

    title: str = Field(description="子话题标题（一期深挖的选题，不带栏目名前缀）")
    hot_item_ids: list[int] = Field(default_factory=list, description="支撑该子话题的素材 hot_item id")


class WeekThemePlan(BaseModel):
    """P5b 周主题规划输出 Schema：深挖栏目每周先定主题，再按子话题分期。

    LLM 从本周采样素材里归纳一个主题并拆出 N 个互补子话题（N = 栏目每周期数），
    解决"每期各写各的、系列无联动"的问题。
    """

    theme: str = Field(description="本周主题，10-20 字，如「AI 视频创作实战周」")
    subtopics: list[SubtopicPlan] = Field(default_factory=list, description="子话题分期安排")
