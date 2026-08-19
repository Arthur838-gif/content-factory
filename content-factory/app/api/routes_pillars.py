"""内容栏目接口（P5）：CRUD + 周排期。

栏目是账号的可持续内容系列（固定名称/角度/节奏/关键词池），
选题台的一次性选题由 plan 按栏目节奏每周派生。
"""
import logging
import shutil

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import Article, Asset, Pillar, Topic, WeekTheme
from ..services import pillar as pillar_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["pillars"])


class PillarIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    angle: str = ""
    domain: str = ""
    slots_per_week: int = Field(1, ge=1, le=7)
    keywords: list[str] = []
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


def _auto_sample_pillar(pillar_id: int) -> None:
    """新建栏目后对该栏目的关键词池定向采样（后台任务，不阻塞创建响应）。

    走 run_collector 同一管线：熔断同样生效、失败同样计数；采样完成前
    页面轮询 /api/pillars/{id}/materials 展示进度。
    """
    from ..collectors import xhs_sample
    from ..collectors.base import CircuitOpenError, run_collector

    try:
        with session_scope() as session:
            pillar = session.get(Pillar, pillar_id)
            keywords = list(pillar.keywords or []) if pillar else []
        if not keywords:
            return
        result = run_collector("xhs_sample", collector=xhs_sample.XhsSampleCollector(keywords=keywords))
        logger.info(
            "新栏目自动采样完成（pillar=%s）：fetched=%s inserted=%s", pillar_id, result.fetched, result.inserted
        )
    except CircuitOpenError as exc:
        logger.warning("新栏目自动采样被熔断拦截（pillar=%s）：%s", pillar_id, exc)
    except Exception:
        # 后台任务异常不冒泡（不影响已创建成功的栏目），熔断告警由 run_collector 负责
        logger.exception("新栏目自动采样失败（pillar=%s）", pillar_id)


@router.post("/pillars", status_code=201)
def create_pillar(body: PillarIn, background: BackgroundTasks) -> dict:
    with session_scope() as session:
        row = Pillar(**body.model_dump())
        session.add(row)
        session.flush()
        result = _pillar_dict(row)
    if config.PILLAR_AUTO_SAMPLE and body.keywords:
        # 栏目先建好（关键词池登记）再采样：persist 时按 URL 去重与领域过滤，
        # 排期时才能命中。采样在响应返回后的后台执行，页面轮询素材数。
        background.add_task(_auto_sample_pillar, row.id)
        result["auto_sampling"] = True
    else:
        result["auto_sampling"] = False
    return result


@router.get("/pillars/{pillar_id}/materials")
def pillar_materials(pillar_id: int) -> dict:
    """本周命中该栏目关键词的素材条数（新建栏目自动采样进度轮询用）。"""
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
