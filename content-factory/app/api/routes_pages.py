"""Jinja 管理页面（P3 / M8）。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from .. import config
from ..db import session_scope
from ..models import Article, Asset, Prompt, Topic

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
