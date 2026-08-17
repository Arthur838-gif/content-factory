"""选题与生成接口（计划书第 8 章 / SDD 4.2 核心接口契约）。

P0 只实现 POST /api/topics/{id}/generate（platform=wechat）。五段式链路：
选题 → 选模板（M4 现读库）→ LLM/Mock（M5）→ JSON 校验 → 落库。
公众号 HTML 渲染与草稿箱推送属 M6，P0 不做；articles 存 Markdown，meta 只写 digest + usage。
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update

from .. import config
from ..db import session_scope
from ..models import Article, Topic
from ..schemas import WechatArticle
from ..services import generator, prompt_engine
from ..services.prompt_engine import PromptNotFoundError, TemplateRenderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["topics"])

# P0 支持的平台与各自的输出 Schema（小红书留 P1）
PLATFORM_SCHEMAS: dict[str, type] = {
    "wechat": WechatArticle,
}


class GenerateResponse(BaseModel):
    """SDD 4.2：成功 {article_id, status:"ready", platform}；失败 {status:"failed", error}。"""

    article_id: int
    status: str
    platform: str
    error: str | None = None


def _build_variables(topic: Topic) -> dict:
    """把 topic 上下文组装成模板变量（附录 A2 用到 title/angle/domain/reference_points）。"""
    reference_points = ""
    evidence = topic.evidence or {}
    items = evidence.get("items") if isinstance(evidence, dict) else None
    if items:
        lines = []
        for it in items:
            if isinstance(it, dict):
                lines.append(f"- {it.get('title', '')}（{it.get('url', '')}）".rstrip("（）"))
        reference_points = "\n".join(lines)
    return {
        "title": topic.title or "",
        "angle": topic.angle or "",
        "domain": topic.domain or "",
        "reference_points": reference_points,
    }


def _has_unarchived_published(session, topic_id: int, platform: str) -> bool:
    """该 (topic_id, platform) 是否已有 published 行未归档（终态，不可覆盖重生成）。

    计划书 5.2 / SDD 5.2：published 是终态，内容不再修改。
    TODO(confirm)：任务四件套与 SDD 4.2 写"已有 ready 未归档返回 409"，但验收脚本 #2/#3 明确
    对同一 topic 连续触发生成并期望"重新生成开新行、旧行归档"成功——二者不可同时成立。
    此处取与验收脚本一致的解释：ready/failed 可重新生成（旧行归档、开新行），published 终态
    不可重生成返回 409。待负责人拍板后据此调整。
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
    platform: str = Query(..., description="生成平台，P0 仅支持 wechat"),
    prompt_id: int | None = Query(None, description="可选，不传取 enabled 且 version 最大者"),
) -> GenerateResponse:
    """触发生成（核心接口，SDD 4.2）。

    成功 200 + {article_id, status:"ready", platform}；
    生成失败不算 HTTP 错误，落 failed 行并 200 + {status:"failed", error}；
    404 topic 不存在；409 该 (topic_id, platform) 已有 published 终态行未归档。
    """
    schema_cls = PLATFORM_SCHEMAS.get(platform)
    if schema_cls is None:
        # P0 只支持 wechat；小红书(xhs)留 P1
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持平台 {platform}；P0 仅支持 wechat，小红书(xhs)留 P1",
        )

    scenario = "article" if platform == "wechat" else "note"

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
        variables = _build_variables(topic)
        try:
            prompt, system_msg, user_msg = prompt_engine.render_messages(
                session, platform, scenario, variables, prompt_id=prompt_id
            )
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TemplateRenderError as exc:
            raise HTTPException(status_code=500, detail=f"模板渲染失败：{exc}") from exc
        prompt_id_resolved = prompt.id
        # 拷贝出纯文本，避免 ORM 对象跨 session
        system_msg, user_msg = system_msg, user_msg

    # 2) 调 LLM / 走 mock（事务外，慢速外部 IO 不占事务）
    result = generator.generate(platform, schema_cls, system_msg, user_msg)

    # 3) 落库：归档旧行 + 写新行 + topic → used（同一事务）
    if result.ok and result.article is not None:
        article_obj: WechatArticle = result.article
        status = "ready"
        title = article_obj.title
        content = article_obj.content_md
        meta = {"digest": article_obj.digest, "usage": result.usage}
        error = None
    else:
        # 生成失败 或 敏感词命中：落 failed 行，error 必填，内容仍保留供排查
        status = "failed"
        title = ""
        content = getattr(result.article, "content_md", "") if result.article else ""
        digest = getattr(result.article, "digest", "") if result.article else ""
        meta = {"usage": result.usage}
        if digest:
            meta["digest"] = digest
        error = result.error or "生成失败"

    with session_scope() as session:
        _archive_existing(session, topic_id, platform)
        article = Article(
            topic_id=topic_id,
            prompt_id=prompt_id_resolved,
            platform=platform,
            title=title,
            content=content,
            tags=None,
            meta=meta,
            status=status,
            error=error,
        )
        session.add(article)
        session.flush()  # 拿 article.id
        article_id = article.id
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
