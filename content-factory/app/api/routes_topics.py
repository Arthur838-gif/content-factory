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
from ..services import generator, imagegen, imaging, model_config, prompt_engine, titles
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
    A1 另用 tag_candidates：取 tag_library 中同领域热度前 10 的标签，冷启动可为空表）。
    P5b：pillar 选题另带系列上下文（周主题/期数/本周其他期/合集篇），
    让每期生成时知道自己在系列中的位置，形成联动而不是各写各的。"""
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
    variables = {
        "title": topic.title or "",
        "angle": topic.angle or "",
        "domain": topic.domain or "",
        "reference_points": reference_points,
        "tag_candidates": list(tag_rows),
        "series_theme": "",
        "series_pillar": "",
        "series_episode": "",
        "series_total": "",
        "series_others": [],
        "series_hub": "",
    }
    if topic.source == "pillar" and isinstance(evidence, dict):
        from ..services import pillar as pillar_service

        week_start, _ = pillar_service.week_bounds()
        siblings = [
            t
            for t in pillar_service._week_pillar_topics(session, week_start)
            if (t.evidence or {}).get("pillar_id") == evidence.get("pillar_id")
            and t.status != "archived"
        ]
        siblings.sort(key=lambda t: t.id)
        variables["series_theme"] = str(evidence.get("week_theme") or "")
        variables["series_pillar"] = str(evidence.get("pillar_name") or "")
        # 期数：优先 evidence 里排期时写好的；老选题（无 episode）按兄弟序推，
        # 但本周没有任何分期标记时不编期数（合集档单独成篇，不是"第 1 期"）
        episode = evidence.get("episode")
        if not episode and any((s.evidence or {}).get("episode") for s in siblings):
            episode = next((i for i, t in enumerate(siblings, 1) if t.id == topic.id), "")
        variables["series_episode"] = episode or ""
        variables["series_total"] = evidence.get("episodes_total") or ""
        variables["series_others"] = [t.title for t in siblings if t.id != topic.id]
        # 合集篇（无 episode 标记）是系列流量枢纽，深挖结尾导流指向它
        variables["series_hub"] = next(
            (t.title for t in siblings if not (t.evidence or {}).get("episode")), ""
        )
    return variables


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

    # 3) 落库（与改写共用）：适配 → 归档旧行 + 写新行 + 出图 + topic → used
    return _persist_generation(
        topic_id, platform, result, prompt_id_resolved, prompt_version_resolved, variables
    )


def _persist_generation(
    topic_id: int,
    platform: str,
    result,
    prompt_id_resolved: int,
    prompt_version_resolved: int,
    variables: dict,
    extra_meta: dict | None = None,
) -> GenerateResponse:
    """生成/改写结果的落库共用段（SDD 4.2 / 5.7 同一语义）。

    适配平台格式 → 两段式封面底图（事务外）→ 归档旧行 + 写新行 +
    xhs 出图登记 assets + topic → used（同一事务）。
    """
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

    if extra_meta:
        meta.update(extra_meta)

    # 两段式封面第一段（事务外慢速 IO）：从文案归纳画面提示词 → cogview-4 底图；
    # 失败返回 None，第二段（PIL 叠字）回退纯色版式，绝不拖 failed
    cover_bg = None
    if status == "ready" and platform == "xhs" and config.IMAGEGEN_ENABLED:
        cover_bg = imagegen.cover_background(
            title=title, content=content, tags=tags or [],
            width=1080, height=1440,  # emotion_cover 画布（3:4 → cogview 864x1152）
        )

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
                    cover_background=cover_bg,
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
        topic_id, platform, article_id, status,
        model_config.mock_enabled(model_config.PURPOSE_TEXT),
    )
    return GenerateResponse(
        article_id=article_id, status=status, platform=platform, error=error
    )


_PLATFORM_LABEL = {"wechat": "公众号", "xhs": "小红书"}


@router.post("/articles/{article_id}/rewrite", response_model=GenerateResponse)
def rewrite_article(
    article_id: int,
    platform: str = Query(..., description="目标平台：wechat / xhs"),
    prompt_id: int | None = Query(None),
) -> GenerateResponse:
    """以成文为源跨平台改写（融合红狐 xiaohongshu-rewrite / multi-rewrite）。

    与从选题重新生成的区别：源是已成的文章，保留事实/案例/数据与观点，
    只做平台风格转换（公众号长文 ↔ 小红书笔记），禁止新增原文没有的信息。
    落库与 generate 同一语义（含 xhs 出图），meta 记 rewrite_from 溯源。
    """
    schema_cls = PLATFORM_SCHEMAS.get(platform)
    if schema_cls is None:
        raise HTTPException(400, f"暂不支持平台 {platform}；当前支持：{'、'.join(PLATFORM_SCHEMAS)}")
    with session_scope() as session:
        source = session.get(Article, article_id)
        if source is None:
            raise HTTPException(404, f"article {article_id} 不存在")
        if source.status != "ready":
            raise HTTPException(422, "只有 ready 状态的文章可以改写")
        if source.platform == platform:
            raise HTTPException(422, f"已是{_PLATFORM_LABEL.get(platform, platform)}文章；换风格请用「重新生成」")
        topic = session.get(Topic, source.topic_id)
        if topic is None:
            raise HTTPException(404, f"topic {source.topic_id} 不存在")
        if _has_unarchived_published(session, topic.id, platform):
            raise HTTPException(409, f"topic {topic.id} 在 {platform} 已有 published 终态行")
        tag_rows = session.scalars(
            select(TagLibrary.tag)
            .where(TagLibrary.domain == (topic.domain or ""))
            .order_by(desc(TagLibrary.heat))
            .limit(10)
        ).all()
        variables = {
            "source_platform": _PLATFORM_LABEL.get(source.platform, source.platform),
            "source_title": source.title or "",
            "source_content": (source.content or "")[:6000],
            "domain": topic.domain or "",
            "tag_candidates": list(tag_rows),
        }
        try:
            prompt, system_msg, user_msg = prompt_engine.render_messages(
                session, platform, "rewrite", variables, prompt_id=prompt_id
            )
        except PromptNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except TemplateRenderError as exc:
            raise HTTPException(500, f"模板渲染失败：{exc}") from exc
    result = generator.generate(platform, schema_cls, system_msg, user_msg)
    logger.info("改写完成 source=%s → %s", article_id, platform)
    return _persist_generation(
        topic.id, platform, result, prompt.id, prompt.version, variables,
        extra_meta={"rewrite_from": article_id},
    )


class TitleScoreIn(BaseModel):
    title: str
    keyword: str = ""


@router.post("/titles/score")
def score_title(body: TitleScoreIn) -> dict:
    """小红书标题六维加权打分（融合红狐 xiaohongshu-title-score 方法论）。

    无状态评审：主题匹配度15% + 结构合规度20% + 利益清晰度25% +
    情绪唤醒度20% + 稀缺性感知15% + 合规安全性5%，S/A/B/C 分级，
    附问题清单与改写版标题。
    """
    try:
        return titles.score(body.title, body.keyword)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"标题打分失败：{exc}") from exc


@router.put("/topics/{topic_id}/archive")
def archive_topic(topic_id: int) -> dict:
    """归档选题（status=archived）：工作台不再展示、不再计入排期期数。

    P5b：确认周主题后想重排本周深挖，先归档旧的各写各的选题；
    已生成的文章不受影响（仍在文章页，只是选题不再排期）。
    """
    with session_scope() as session:
        topic = session.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(404, f"topic {topic_id} 不存在")
        topic.status = "archived"
        return {"id": topic_id, "status": "archived"}


class TitleIn(BaseModel):
    title: str


@router.put("/topics/{topic_id}/title")
def rename_topic(topic_id: int, body: TitleIn) -> dict:
    """改选题标题：排期标题是自动生成的（栏目名/子话题），与最终成文标题
    不必一致——编辑在发布前按平台调性改标题，生成时即用新标题。"""
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "标题不能为空")
    with session_scope() as session:
        topic = session.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(404, f"topic {topic_id} 不存在")
        topic.title = title[:512]
        return {"id": topic_id, "title": topic.title}
