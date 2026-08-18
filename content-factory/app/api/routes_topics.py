"""选题与生成接口（计划书第 8 章 / SDD 4.2 核心接口契约）。

P0：POST /api/topics/{id}/generate?platform=wechat（公众号 Markdown）。
P1：platform=xhs（小红书笔记，M7 适配后正文末尾拼 #标签）。
P2：xhs 成功后自动出图（M7 调共享图文服务，1 封面 + N 金句图，登记 assets；
出图失败 article 整体 failed——SDD 5.7 articles 与 assets 同一事务）。
五段式链路：选题 → 选模板（M4 现读库）→ LLM/Mock（M5）→ JSON 校验 → 适配落库。
公众号 HTML 渲染与草稿箱推送属 M6、素材包 ZIP 与预览页属 P3，均不做。
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select, update

from .. import config
from ..adapters import xhs as xhs_adapter
from ..db import session_scope
from ..models import Article, TagLibrary, Topic
from ..schemas import WechatArticle, XhsNote
from ..services import generator, imaging, prompt_engine
from ..services.prompt_engine import PromptNotFoundError, TemplateRenderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["topics"])


@router.get("/topics")
def list_topics(
    limit: int = Query(200, ge=1, le=1000, description="分页上限，防全表膨胀拖垮响应"),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """选题列表，按 score 倒序（P4：回填驱动的评分即时体现在排序上）。"""
    with session_scope() as session:
        topics = session.scalars(
            select(Topic).order_by(desc(Topic.score), desc(Topic.id)).limit(limit).offset(offset)
        ).all()
        return [
            {
                "id": t.id,
                "title": t.title,
                "angle": t.angle,
                "domain": t.domain,
                "source": t.source,
                "status": t.status,
                "score": t.score,
                "evidence": t.evidence,
                "expires_at": t.expires_at,
                "created_at": t.created_at,
            }
            for t in topics
        ]

# 支持的平台与各自的输出 Schema（wechat P0，xhs P1）
PLATFORM_SCHEMAS: dict[str, type] = {
    "wechat": WechatArticle,
    "xhs": XhsNote,
}

# 平台 → scenario（模板选择键：wechat=article 长文，xhs=note 笔记）
PLATFORM_SCENARIOS: dict[str, str] = {
    "wechat": "article",
    "xhs": "note",
}


class GenerateResponse(BaseModel):
    """SDD 4.2：成功 {article_id, status:"ready", platform}；失败 {status:"failed", error}。"""

    article_id: int
    status: str
    platform: str
    error: str | None = None


def _build_variables(session, topic: Topic) -> dict:
    """把 topic 上下文组装成模板变量（A2 用 title/angle/domain/reference_points，
    A1 另用 tag_candidates：取 tag_library 中同领域热度前 10 的标签，冷启动可为空表）。"""
    reference_points = ""
    evidence = topic.evidence or {}
    items = evidence.get("items") if isinstance(evidence, dict) else None
    if items:
        lines = []
        for it in items:
            if isinstance(it, dict):
                lines.append(f"- {it.get('title', '')}（{it.get('url', '')}）".rstrip("（）"))
        reference_points = "\n".join(lines)
    tag_rows = session.scalars(
        select(TagLibrary.tag)
        .where(TagLibrary.domain == (topic.domain or ""))
        .order_by(desc(TagLibrary.heat))
        .limit(10)
    ).all()
    return {
        "title": topic.title or "",
        "angle": topic.angle or "",
        "domain": topic.domain or "",
        "reference_points": reference_points,
        "tag_candidates": list(tag_rows),
    }


def _has_unarchived_published(session, topic_id: int, platform: str) -> bool:
    """该 (topic_id, platform) 是否已有 published 行未归档（终态，不可覆盖重生成）。

    计划书 5.2 / SDD 5.2：published 是终态，内容不再修改。
    已拍板（P1 任务四件套）：ready/failed 可重新生成（旧行归档、开新行），
    只有 published 终态行返回 409；SDD 4.2 的"已有 ready 行返回 409"为笔误。
    """
    row = session.scalars(
        select(Article.id).where(
            Article.topic_id == topic_id,
            Article.platform == platform,
            Article.status == "published",
        )
    ).first()
    return row is not None


def _archive_existing(session, topic_id: int, platform: str) -> int:
    """重新生成开新行：把同 (topic_id, platform) 的 ready/failed 旧行 → archived。"""
    result = session.execute(
        update(Article)
        .where(
            Article.topic_id == topic_id,
            Article.platform == platform,
            Article.status.in_(["ready", "failed"]),
        )
        .values(status="archived")
    )
    return result.rowcount or 0


@router.post("/topics/{topic_id}/generate", response_model=GenerateResponse)
def generate(
    topic_id: int,
    platform: str = Query(..., description="生成平台：wechat（P0）/ xhs（P1）"),
    prompt_id: int | None = Query(None, description="可选，不传取 enabled 且 version 最大者"),
) -> GenerateResponse:
    """触发生成（核心接口，SDD 4.2）。

    成功 200 + {article_id, status:"ready", platform}；
    生成失败不算 HTTP 错误，落 failed 行并 200 + {status:"failed", error}；
    404 topic 不存在；409 该 (topic_id, platform) 已有 published 终态行未归档。
    """
    schema_cls = PLATFORM_SCHEMAS.get(platform)
    if schema_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持平台 {platform}；当前支持：{'、'.join(PLATFORM_SCHEMAS)}",
        )

    scenario = PLATFORM_SCENARIOS[platform]

    # 1) 读 topic + 冲突预检（事务外只读，生成前避免浪费 LLM 调用）
    with session_scope() as session:
        topic = session.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"topic {topic_id} 不存在")
        if _has_unarchived_published(session, topic_id, platform):
            raise HTTPException(
                status_code=409,
                detail=f"topic {topic_id} 在 {platform} 已有 published 终态行，不可重新生成",
            )
        variables = _build_variables(session, topic)
        try:
            prompt, system_msg, user_msg = prompt_engine.render_messages(
                session, platform, scenario, variables, prompt_id=prompt_id
            )
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TemplateRenderError as exc:
            raise HTTPException(status_code=500, detail=f"模板渲染失败：{exc}") from exc
        prompt_id_resolved = prompt.id
        prompt_version_resolved = prompt.version

    # 2) 调 LLM / 走 mock（事务外，慢速外部 IO 不占事务）
    result = generator.generate(platform, schema_cls, system_msg, user_msg)

    # 3) 落库：归档旧行 + 写新行 + topic → used（同一事务）
    art = result.article
    if result.ok and art is not None:
        status = "ready"
        if platform == "xhs":
            # M7 文案适配：正文末尾拼 #标签，meta 存 cover_note + image_plan
            formatted = xhs_adapter.format_note(art)
            title, content, tags, meta = formatted.title, formatted.content, formatted.tags, formatted.meta
        else:
            title = art.title
            content = art.content_md
            tags = None
            meta = {"digest": art.digest}
        # P4：模板效果分归因凭据（不改表；列上已有 prompt_id，meta 再存版本快照防模板行变动）
        meta["prompt_id"] = prompt_id_resolved
        meta["prompt_version"] = prompt_version_resolved
        meta["usage"] = result.usage
        error = None
    else:
        # 生成失败 或 敏感词命中：落 failed 行，error 必填，内容仍保留供排查
        status = "failed"
        title = getattr(art, "title", "") if art else ""
        content = (getattr(art, "content_md", "") or getattr(art, "content", "")) if art else ""
        tags = list(art.tags) if art and getattr(art, "tags", None) else None
        meta = {
            "prompt_id": prompt_id_resolved,
            "prompt_version": prompt_version_resolved,
            "usage": result.usage,
        }
        digest = getattr(art, "digest", "") if art else ""
        if digest:
            meta["digest"] = digest
        cover = getattr(art, "cover_text", "") if art else ""
        if cover:
            meta["cover_note"] = cover
        error = result.error or "生成失败"

    with session_scope() as session:
        _archive_existing(session, topic_id, platform)
        article = Article(
            topic_id=topic_id,
            prompt_id=prompt_id_resolved,
            platform=platform,
            title=title,
            content=content,
            tags=tags,
            meta=meta,
            status=status,
            error=error,
        )
        session.add(article)
        session.flush()  # 拿 article.id
        article_id = article.id
        if status == "ready" and platform == "xhs":
            # P2：图文合成在落库事务内执行（SDD 5.7 不留"有文章无资产"的半成品）；
            # 出图失败（含字体缺失）→ article 整体 failed，与 LLM 失败同一落库语义。
            try:
                xhs_adapter.render_assets(
                    session,
                    article_id,
                    # cover_text 为空时兜底用标题，保证素材包首图恒为 01_cover（SDD 6.3）
                    cover_note=meta.get("cover_note") or title,
                    image_plan=meta.get("image_plan") or [],
                    footer_text=variables.get("domain") or "",
                )
            except imaging.ImagingError as exc:
                status = "failed"
                article.status = "failed"
                article.error = error = f"图文合成失败：{exc}"
        # topic 首次触发生成 → used
        session.execute(
            update(Topic).where(Topic.id == topic_id, Topic.status == "new").values(status="used")
        )

    logger.info(
        "生成完成 topic=%s platform=%s article=%s status=%s mock=%s",
        topic_id, platform, article_id, status, config.LLM_MOCK,
    )
    return GenerateResponse(
        article_id=article_id, status=status, platform=platform, error=error
    )
