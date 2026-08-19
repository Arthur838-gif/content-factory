"""内容栏目接口（P5）：CRUD + 周排期。

栏目是账号的可持续内容系列（固定名称/角度/节奏/关键词池），
选题台的一次性选题由 plan 按栏目节奏每周派生。
"""
import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import Article, Asset, Pillar, Topic, WeekTheme
from ..services import domain_service, pillar as pillar_service, sampling_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["pillars"])


class PillarIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    angle: str = ""
    domain: str = Field("", max_length=64, description="领域名（官方类目可下拉、新领域可手填）")
    slots_per_week: int = Field(1, ge=1, le=7)
    keywords: list[Annotated[str, StringConstraints(max_length=64, strip_whitespace=True)]] = []
    active: bool = True


def _pillar_dict(p: Pillar) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "angle": p.angle,
        "domain": p.domain,
        "slots_per_week": p.slots_per_week,
        "keywords": p.keywords or [],
        "active": p.active,
        "created_at": p.created_at,
    }


@router.get("/pillars")
def list_pillars() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(Pillar).order_by(Pillar.id)).all()
        return [_pillar_dict(p) for p in rows]


@router.post("/pillars", status_code=201)
def create_pillar(body: PillarIn) -> dict:
    # 领域登记与栏目插入同一事务（P-2）：词表与栏目要么都落库要么都不落，
    # 消除 YAML 时代"词表写成功、栏目失败"或反过来的双写不一致。
    # 领域不在词表（官方类目或手填新领域）→ 连同栏目关键词登记，否则
    # 采集入库时标题命不中任何领域会被整批过滤（每日一句踩过的坑）。
    with session_scope() as session:
        if body.domain and body.keywords:
            try:
                domain_service.upsert_domain(session, body.domain, body.keywords, source="user")
            except ValueError as exc:
                # 长度非法已在 Pydantic 拦截，这里只兜底记录，不挡栏目创建
                logger.warning("领域登记被拒（domain=%s）：%s", body.domain, exc)
        row = Pillar(**body.model_dump())
        session.add(row)
        session.flush()
        result = _pillar_dict(row)
        pillar_id = row.id
    # P-2：自动采样只入队（快照关键词 + pillar 去重键），立即返回 job_id；
    # 付费网络请求由 worker 进程执行，页面按 /api/sampling/jobs/{id} 轮询真实进度。
    result["sampling_job_id"] = None
    if config.PILLAR_AUTO_SAMPLE and body.keywords:
        try:
            job, _ = sampling_jobs.enqueue(
                kind="pillar", keywords=body.keywords,
                pillar_id=pillar_id, dedupe_key=f"pillar:{pillar_id}",
            )
            result["sampling_job_id"] = job.id
        except ValueError as exc:
            logger.warning("栏目自动采样入队失败（pillar=%s）：%s", pillar_id, exc)
    return result


@router.get("/pillars/{pillar_id}/materials")
def pillar_materials(pillar_id: int) -> dict:
    """本周可排期素材数（P-2 起：只表示素材储备，采样任务进度看 /api/sampling/jobs/{id}）。"""
    with session_scope() as session:
        pillar = session.get(Pillar, pillar_id)
        if pillar is None:
            raise HTTPException(404, f"栏目 {pillar_id} 不存在")
        return {
            "matched": pillar_service.matched_pool_size(session, pillar),
            "min_required": pillar_service.COLLECTION_MIN_MATERIALS,
        }


@router.put("/pillars/{pillar_id}")
def update_pillar(pillar_id: int, body: PillarIn) -> dict:
    with session_scope() as session:
        row = session.get(Pillar, pillar_id)
        if row is None:
            raise HTTPException(404, f"栏目 {pillar_id} 不存在")
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        return _pillar_dict(row)


@router.delete("/pillars/{pillar_id}")
def delete_pillar(pillar_id: int, force: bool = False) -> dict:
    """删除栏目，连带清理它名下的排期选题、周主题（工作台同步消失）。

    选题已生成文章时默认拒绝（409）；force=true 确认后连文章、
    配图文件一并永久删除。页面二次确认后带 force 重试。
    """
    with session_scope() as session:
        row = session.get(Pillar, pillar_id)
        if row is None:
            raise HTTPException(404, f"栏目 {pillar_id} 不存在")
        topics = [
            t for t in session.scalars(select(Topic).where(Topic.source == "pillar")).all()
            if (t.evidence or {}).get("pillar_id") == pillar_id
        ]
        articles = [
            a for t in topics
            for a in session.scalars(select(Article).where(Article.topic_id == t.id)).all()
        ]
        if articles and not force:
            raise HTTPException(
                409,
                f"该栏目选题已有 {len(articles)} 篇生成文章；"
                "如确认连文章与配图一起永久删除，请带 force=true 重试",
            )
        for a in articles:
            shutil.rmtree(config.ASSETS_DIR / str(a.id), ignore_errors=True)
            for asset in session.scalars(select(Asset).where(Asset.article_id == a.id)).all():
                session.delete(asset)
            session.flush()  # assets 先落，再删 article（无映射关系时 flush 顺序不保证）
            session.delete(a)
        session.flush()
        for t in topics:
            session.delete(t)
        for theme in session.scalars(
            select(WeekTheme).where(WeekTheme.pillar_id == pillar_id)
        ).all():
            session.delete(theme)
        session.flush()  # 再落选题/主题，避免 week_themes 外键挡住栏目删除
        session.delete(row)
        return {
            "deleted": pillar_id,
            "topics_removed": len(topics),
            "articles_removed": len(articles),
        }


@router.post("/pillars/plan")
def plan_pillars(
    pillar_id: int | None = None, replan_theme: bool = False
) -> list[dict]:
    """生成本周内容计划：全部启用栏目，或指定栏目（body 可省）。

    幂等：重复调用只补缺口（多期档）/ 直接跳过（周更档）。
    深挖栏目若已确认本周主题，则按主题子话题分期（P5b）。
    replan_theme=true：一键按主题重排——先归档未按主题排期的旧选题再补齐
    （用于本周已有旧排期、确认主题后想整体套用主题的场景）。
    """
    try:
        return pillar_service.plan_week(pillar_id=pillar_id, replan_theme=replan_theme)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


class ThemeConfirmIn(BaseModel):
    theme: str | None = None
    subtopics: list[dict] | None = None


@router.post("/pillars/{pillar_id}/theme")
def generate_theme(pillar_id: int) -> dict:
    """从本周采样素材规划周主题（LLM 归纳主题 + 子话题分期，status=proposed）。"""
    with session_scope() as session:
        pillar = session.get(Pillar, pillar_id)
        if pillar is None:
            raise HTTPException(404, f"栏目 {pillar_id} 不存在")
        try:
            row = pillar_service.generate_week_theme(session, pillar)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None
        return _theme_dict(row)


@router.put("/pillars/{pillar_id}/theme")
def confirm_theme(pillar_id: int, body: ThemeConfirmIn) -> dict:
    """确认本周主题（可顺带改写 theme/subtopics 文案），确认后排期按主题分期。"""
    with session_scope() as session:
        pillar = session.get(Pillar, pillar_id)
        if pillar is None:
            raise HTTPException(404, f"栏目 {pillar_id} 不存在")
        try:
            row = pillar_service.confirm_week_theme(
                session, pillar, theme=body.theme, subtopics=body.subtopics
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return _theme_dict(row)


def _theme_dict(row) -> dict:
    return {
        "id": row.id,
        "pillar_id": row.pillar_id,
        "week_start": row.week_start,
        "theme": row.theme,
        "subtopics": row.subtopics or [],
        "status": row.status,
    }
