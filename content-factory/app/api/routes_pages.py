"""Jinja 管理页面（P3 / M8；P-1b 加低粉爆款页，P4 加报表页）。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from .. import config
from ..collectors import base as collectors_base
from ..db import session_scope
from ..models import Article, Asset, Prompt, Topic
from ..services import radar, scoring
from .routes_prompts import _prompt_dict

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def _article_view(session, article: Article) -> dict:
    assets = session.scalars(select(Asset).where(Asset.article_id == article.id).order_by(Asset.id)).all()
    # filename 单独给出：模板不再自行 split(asset.path)，改资产存储结构只动这里
    return {
        "article": article,
        "assets": [{**asset.__dict__, "filename": Path(asset.path).name} for asset in assets],
    }


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


@router.get("/stats")
def stats_page(request: Request, month: str | None = None):
    """P4 报表页：成本统计、模板效果、阈值校准三区（只读，不提供改阈值入口）。"""
    target = scoring.normalize_month(month)
    if target is None:
        raise HTTPException(status_code=422, detail="month 格式应为真实月份 YYYY-MM")
    cost = scoring.cost_report(target)
    prompt_stats = scoring.prompt_stats()
    calibration = scoring.calibration_view()
    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "cost": cost,
            "prompt_stats": prompt_stats,
            "calibration": calibration,
            # 阈值初值由 config 下发，模板不再硬编码数字
            "prompt_stats_min": config.PROMPT_STATS_MIN_SAMPLES,
        },
    )


@router.get("/viral")
def viral_page(request: Request):
    """低粉爆款管理页：采集器状态、人工喂样本表单与样本列表（P-1b）。"""
    with session_scope() as session:
        rows = session.execute(radar.query_viral_samples(session).limit(100)).all()
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
            {**_prompt_dict(prompt),
             "updated_at": prompt.updated_at.strftime("%Y-%m-%d %H:%M") if prompt.updated_at else ""}
            for prompt in prompt_rows
        ]
    return templates.TemplateResponse(request=request, name="prompts.html", context={"prompts": prompts})
