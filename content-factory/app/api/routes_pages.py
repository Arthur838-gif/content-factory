"""Jinja 管理页面（P3 / M8；P-1b 加低粉爆款页）。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from .. import config
from ..collectors import base as collectors_base
from ..db import session_scope
from ..models import Article, Asset, HotItem, Prompt, Topic, ViralSample
from ..services import radar

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(config.PROJECT_ROOT / "app" / "templates"))


def _article_view(session, article: Article) -> dict:
    assets = session.scalars(select(Asset).where(Asset.article_id == article.id).order_by(Asset.id)).all()
    return {"article": article, "assets": assets}


@router.get("/")
def topics_page(request: Request):
    with session_scope() as session:
        topics = session.scalars(select(Topic).order_by(desc(Topic.score), desc(Topic.id))).all()
        articles = session.scalars(select(Article).order_by(desc(Article.created_at), desc(Article.id))).all()
        by_topic: dict[int, dict[str, Article]] = {}
        for article in articles:
            by_topic.setdefault(article.topic_id, {}).setdefault(article.platform, article)
    return templates.TemplateResponse(
        request=request,
        name="topics.html",
        context={"topics": topics, "latest": by_topic},
    )


@router.get("/articles/{article_id}")
def article_page(request: Request, article_id: int):
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
        view = _article_view(session, article)
    return templates.TemplateResponse(request=request, name="article.html", context=view)


@router.get("/viral")
def viral_page(request: Request):
    """低粉爆款管理页：采集器状态、人工喂样本表单与样本列表（P-1b）。"""
    with session_scope() as session:
        rows = session.execute(
            select(ViralSample, HotItem)
            .join(HotItem, ViralSample.hot_item_id == HotItem.id)
            .order_by(desc(ViralSample.viral_score), desc(ViralSample.id))
            .limit(100)
        ).all()
        samples = [
            {"item": i, "domain": s.domain, "viral_score": s.viral_score,
             "title_pattern": s.title_pattern, "reason": s.reason,
             "created_at": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else ""}
            for s, i in rows
        ]
    states = collectors_base.collector_status()
    return templates.TemplateResponse(
        request=request,
        name="viral.html",
        context={"samples": samples, "states": states, "domains": list(radar.load_domains())},
    )


@router.get("/prompts")
def prompts_page(request: Request):
    with session_scope() as session:
        prompt_rows = session.scalars(
            select(Prompt).order_by(Prompt.platform, Prompt.scenario, desc(Prompt.version))
        ).all()
        prompts = [
            {
                "id": prompt.id,
                "platform": prompt.platform,
                "scenario": prompt.scenario,
                "name": prompt.name,
                "template": prompt.template,
                "variables": prompt.variables or [],
                "version": prompt.version,
                "enabled": prompt.enabled,
                "updated_at": prompt.updated_at.strftime("%Y-%m-%d %H:%M") if prompt.updated_at else "",
            }
            for prompt in prompt_rows
        ]
    return templates.TemplateResponse(request=request, name="prompts.html", context={"prompts": prompts})
