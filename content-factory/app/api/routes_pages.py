"""Jinja 管理页面（P3 / M8；P-1b 加低粉爆款页，P4 加报表页）。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from .. import config
from ..collectors import base as collectors_base
from ..db import session_scope
from ..models import Article, Asset, CollectorState, Pillar, Prompt, Topic
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
    """工作台（P5 改版）：本周栏目排期为主线，一次性灵感选题为备选池。

    数据分两组：pillar 排期选题（本周，按栏目分组）与 radar/manual 灵感选题
    （按 score 倒序）；顶部给每栏目本周进度（已排/已生成/已发布）与采样器状态。
    """
    from datetime import datetime

    from ..models import CollectorState, Pillar
    from ..services import pillar as pillar_service

    with session_scope() as session:
        topics = session.scalars(select(Topic).order_by(desc(Topic.score), desc(Topic.id))).all()
        articles = session.scalars(select(Article).order_by(desc(Article.created_at), desc(Article.id))).all()
        by_topic: dict[int, dict[str, Article]] = {}
        for article in articles:
            by_topic.setdefault(article.topic_id, {}).setdefault(article.platform, article)
        pillars = session.scalars(select(Pillar).order_by(Pillar.id)).all()
        pillar_map = {p.id: p for p in pillars}
        week_start, week_end = pillar_service.week_bounds()

        pillar_topics = [
            t for t in topics
            if t.source == "pillar" and t.created_at >= week_start and t.status != "archived"
        ]
        # 过期未用的 pillar 选题（上周遗留）单独归入灵感池底部，不混进本周排期
        stale_pillar = [
            t for t in topics
            if t.source == "pillar" and t.created_at < week_start and t.status != "archived"
        ]
        idea_topics = [t for t in topics if t.source != "pillar" and t.status != "archived"] + stale_pillar

        grouped: list[tuple[Pillar, list[Topic]]] = []
        for p in pillars:
            ts = [t for t in pillar_topics if (t.evidence or {}).get("pillar_id") == p.id]
            grouped.append((p, ts))
        # evidence 指向已删除栏目的排期选题（罕见）也保留展示
        known_pids = set(pillar_map)
        orphan = [t for t in pillar_topics if (t.evidence or {}).get("pillar_id") not in known_pids]
        if orphan:
            grouped.append((None, orphan))

        progress = []
        for p in pillars:
            ts = [t for t in pillar_topics if (t.evidence or {}).get("pillar_id") == p.id]
            generated = sum(1 for t in ts if by_topic.get(t.id))
            published = sum(
                1 for t in ts
                if any(a.status == "published" for a in by_topic.get(t.id, {}).values())
            )
            progress.append(
                {"pillar": p, "planned": len(ts), "slots": p.slots_per_week,
                 "generated": generated, "published": published}
            )

        collectors = [
            {"name": row.name, "status": row.status, "short": label}
            for row in session.scalars(
                select(CollectorState).where(
                    CollectorState.name.in_(("xhs_sample", "github_tools"))
                )
            ).all()
            for label in [_WORKBENCH_LABELS.get(row.name, row.name)]
        ]
        # P5b 周主题：分组头展示（深挖联动的主线）
        from ..models import WeekTheme

        themes = {
            th.pillar_id: th
            for th in session.scalars(
                select(WeekTheme).where(WeekTheme.week_start == week_start)
            ).all()
        }
    return templates.TemplateResponse(
        request=request,
        name="topics.html",
        context={
            "topics": topics,
            "latest": by_topic,
            "grouped": grouped,
            "idea_topics": idea_topics,
            "progress": progress,
            "collectors": collectors,
            "xhs_scheduled": config.XHS_SAMPLE_SCHEDULED,
            "themes": themes,
            "now": datetime.now(),
            "week_range": f"{week_start.strftime('%m.%d')}–{week_end.strftime('%m.%d')}",
        },
    )


@router.get("/pillars")
def pillars_page(request: Request):
    """栏目管理页（P5）：列表 + 建栏目表单 + 生成本周计划。"""
    from ..services import pillar as pillar_service

    with session_scope() as session:
        pillars = session.scalars(select(Pillar).order_by(Pillar.id)).all()
        week_start, week_end = pillar_service.week_bounds()
        planned: dict[int, int] = {}
        for topic in session.scalars(
            select(Topic).where(Topic.source == "pillar", Topic.created_at >= week_start)
        ).all():
            if topic.status == "archived":
                continue  # 按主题重排归档的旧选题不占本周档位
            pid = (topic.evidence or {}).get("pillar_id")
            if pid is not None:
                planned[pid] = planned.get(pid, 0) + 1
        # P5b 周主题卡片：建议（proposed）待确认，确认（confirmed）后按主题分期
        from ..models import WeekTheme

        themes = {
            th.pillar_id: th
            for th in session.scalars(
                select(WeekTheme).where(WeekTheme.week_start == week_start)
            ).all()
        }
    return templates.TemplateResponse(
        request=request,
        name="pillars.html",
        context={
            "pillars": pillars,
            "planned": planned,
            "themes": themes,
            "week_range": f"{week_start.strftime('%m.%d')}–{week_end.strftime('%m.%d')}",
            # 领域词表：表单领域下拉 + 关键词推荐标签（新手不用猜填什么）
            "domains": radar.load_domains(),
        },
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


# 采集器标识 → 页面展示用的文字描述（/viral 页）
COLLECTOR_LABELS = {
    "hotboard": "热榜采集（微博/知乎/百度，免费，每小时定时）",
    "xhs_sample": "小红书采样（RedFox 计费优先、失败降级本地 mcp）",
    "github_tools": "GitHub 开源项目采集（免费，只收近 90 天新锐项目）",
    "xhs_teardown": "低粉爆款周度拆解（LLM 分析库内样本，每周一 06:00）",
}

# 工作台采样引擎卡片的简短名（卡片空间小，不放长描述）
_WORKBENCH_LABELS = {
    "xhs_sample": "小红书采样（RedFox 优先）",
    "github_tools": "GitHub 新锐项目采集",
}


def _viral_labels() -> dict:
    """xhs_sample 的触发口径随 CF_XHS_SAMPLE_SCHEDULED 联动，避免文案与配置打架。"""
    labels = dict(COLLECTOR_LABELS)
    if config.XHS_SAMPLE_SCHEDULED:
        labels["xhs_sample"] += f"（每 {config.XHS_SAMPLE_INTERVAL_HOURS} 小时定时 + 手动触发）"
    else:
        labels["xhs_sample"] += "（手动触发）"
    return labels


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
        context={"samples": samples, "states": states, "labels": _viral_labels(),
                 "scheduled": config.XHS_SAMPLE_SCHEDULED,
                 "interval_hours": config.XHS_SAMPLE_INTERVAL_HOURS,
                 "domains": list(radar.load_domains())},
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
