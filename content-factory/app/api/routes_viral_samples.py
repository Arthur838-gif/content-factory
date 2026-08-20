"""低粉爆款样本接口（P-1b / M3）：样本列表 + 人工喂样本降级入口。

P9：公众号人工喂样本走 URL 抓取式（RedFox 详情接口，1 次计费）。
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import session_scope
from ..models import HotItem as HotItemORM
from ..models import ViralSample
from ..schemas import ManualGzhSampleInput, ManualSampleInput
from ..services import domain_service, radar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["viral-samples"])


@router.get("/viral-samples")
def list_viral_samples(domain: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """按 viral_score 倒序返回样本，附 hot_item 关键互动数据（evidence 摘要）。

    gzh 样本额外带 reads/watches/shares（判定与展示都依赖阅读量口径）。
    """
    with session_scope() as session:
        rows = session.execute(
            radar.query_viral_samples(session, domain=domain).limit(limit)
        ).all()
        results = []
        for sample, item in rows:
            hot_item = {
                "id": item.id,
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "author": item.author,
                "fans": item.fans,
                "likes": item.likes,
                "collects": item.collects,
                "comments": item.comments,
            }
            if item.source == "gzh":
                article = radar._gzh_article(item)
                hot_item.update(
                    {
                        "reads": radar._gzh_metric(article, "readCount"),
                        "watches": radar._gzh_metric(article, "watchCount"),
                        "shares": radar._gzh_metric(article, "shareCount"),
                    }
                )
            results.append(
                {
                    "id": sample.id,
                    "domain": sample.domain,
                    "viral_score": sample.viral_score,
                    "title_pattern": sample.title_pattern,
                    "reason": sample.reason,
                    "created_at": sample.created_at.isoformat(timespec="seconds"),
                    "hot_item": hot_item,
                }
            )
        return results


@router.post("/viral-samples/manual", status_code=201)
def create_manual_sample(payload: ManualSampleInput) -> dict:
    """人工喂样本（降级模式入口）：与自动样本走同一打分、落库、撞题与建题管线。

    fans 缺失或互动数字非法由 Pydantic 拦截（422）；URL 重复返回 409，不写半成品。
    领域自由填写：词表里的领域用于展示候选，手填新领域直接按原样落库标注。
    """
    with session_scope() as session:
        existed = session.scalars(
            select(HotItemORM.id).where(HotItemORM.url == payload.url)
        ).first()
        if existed:
            raise HTTPException(
                status_code=409, detail=f"该笔记 URL 已入库（hot_item_id={existed}），不重复录入"
            )
        row = HotItemORM(
            source="xhs",
            title=payload.title,
            url=payload.url,
            author=payload.author,
            fans=payload.fans,
            likes=payload.likes,
            collects=payload.collects,
            comments=payload.comments,
            raw={"entry": "manual"},
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            # URL 唯一约束兜底：检查与插入之间可能有并发写入，命中即 409 回滚
            raise HTTPException(
                status_code=409, detail=f"该笔记 URL 已入库，不重复录入（{payload.url}）"
            ) from exc

        matched = domain_service.match_domain(payload.title, session=session)
        keyword = matched[1] if matched else "manual"
        outcome = radar.process_xhs_item(session, row, payload.domain, keyword, auto=False)
        result = {
            "hot_item_id": row.id,
            "viral_sample_id": outcome["viral_sample_id"],
            "topic_id": outcome["topic_id"],
            "viral": outcome["viral"],
            "viral_score": outcome["viral_score"],
        }

    if result["viral"]:
        logger.info(
            "人工样本入选低粉爆款：hot_item=%s viral_sample=%s topic=%s score=%s",
            result["hot_item_id"], result["viral_sample_id"], result["topic_id"], result["viral_score"],
        )
    else:
        logger.info("人工样本未达入选阈值：hot_item=%s", result["hot_item_id"])
    return result


@router.post("/viral-samples/gzh-manual", status_code=201)
def create_manual_gzh_sample(payload: ManualGzhSampleInput) -> dict:
    """人工喂公众号样本（URL 抓取式，P9）：调 RedFox 优质库详情接口取
    全量指标与正文（1 次计费，页面 confirm 后才触发），与自动采样走
    同一打分、落库、撞题与建题管线。

    URL 重复返回 409（计费调用前先查，省一次白花）；RedFox 失败 502，
    不写半成品。领域与人工喂笔记同口径：下拉候选或手填。
    """
    from ..collectors import redfox

    with session_scope() as session:
        existed = session.scalars(
            select(HotItemORM.id).where(HotItemORM.url == payload.url)
        ).first()
        if existed:
            raise HTTPException(
                status_code=409, detail=f"该文章 URL 已入库（hot_item_id={existed}），不重复录入"
            )

    # 计费调用放事务外：失败不占连接、不留半成品
    try:
        article = redfox.gzh_article_detail(payload.url)
    except redfox.RedFoxError as exc:
        raise HTTPException(status_code=502, detail=f"公众号详情抓取失败：{exc}") from None

    article = dict(article)
    # 详情接口未必回带 workUrl：以请求地址兜底（URL 是去重键）
    article.setdefault("workUrl", payload.url)
    items = redfox.parse_gzh_articles([article])
    if not items:
        raise HTTPException(
            status_code=422, detail="详情接口返回的内容缺少标题，无法入库"
        )
    item = items[0]

    with session_scope() as session:
        raw = dict(item.raw or {})
        raw["entry"] = "manual_url"
        row = HotItemORM(
            source="gzh",
            title=item.title,
            url=item.url,
            author=item.author,
            fans=item.fans,
            likes=item.likes,
            collects=item.collects,
            comments=item.comments,
            raw=raw,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            # URL 唯一约束兜底：检查与插入之间可能有并发写入，命中即 409 回滚
            raise HTTPException(
                status_code=409, detail=f"该文章 URL 已入库，不重复录入（{payload.url}）"
            ) from exc

        matched = domain_service.match_domain(row.title, session=session)
        keyword = matched[1] if matched else "manual"
        outcome = radar.process_gzh_item(session, row, payload.domain, keyword, auto=False)
        result = {
            "hot_item_id": row.id,
            "viral_sample_id": outcome["viral_sample_id"],
            "topic_id": outcome["topic_id"],
            "viral": outcome["viral"],
            "viral_score": outcome["viral_score"],
        }

    if result["viral"]:
        logger.info(
            "人工公众号样本入选爆款：hot_item=%s viral_sample=%s topic=%s score=%s",
            result["hot_item_id"], result["viral_sample_id"], result["topic_id"], result["viral_score"],
        )
    else:
        logger.info("人工公众号样本未达入选阈值：hot_item=%s", result["hot_item_id"])
    return result
