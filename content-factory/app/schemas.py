"""Pydantic 模型。P-1a 只需要 HotItem 与采集结果；WechatArticle 在 P0 补，XhsNote 留 P1。"""
import hashlib
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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


class WechatArticle(BaseModel):
    """公众号输出 Schema（计划书 6.1 / SDD 5.6，M5 与适配层之间的契约）。

    LLM 只产出这三个字段的结构化 JSON；HTML 渲染与草稿箱推送属 M6，P0 不做。
    """

    title: str = Field(..., description="信息密度高的标题，≤ 30 字")
    digest: str = Field(..., description="摘要，≤ 54 字")
    content_md: str = Field(..., description="Markdown 正文，1500-3000 字，含小标题分级")
