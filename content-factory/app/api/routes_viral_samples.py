"""低粉爆款样本接口（P-1b / M3）：样本列表 + 人工喂样本降级入口。"""
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import session_scope
from ..models import HotItem as HotItemORM
from ..models import ViralSample
from ..schemas import ManualSampleInput
from ..services import domain_service, radar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["viral-samples"])


@router.get("/viral-samples")
def list_viral_samples(domain: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """按 viral_score 倒序返回样本，附 hot_item 关键互动数据（evidence 摘要）。"""
    with session_scope() as session:
        rows = session.execute(
            radar.query_viral_samples(session, domain=domain).limit(limit)
        ).all()
        return [
            {
                "id": sample.id,
                "domain": sample.domain,
                "viral_score": sample.viral_score,
                "title_pattern": sample.title_pattern,
                "reason": sample.reason,
                "created_at": sample.created_at.isoformat(timespec="seconds"),
                "hot_item": {
                    "id": item.id,
                    "title": item.title,
                    "url": item.url,
                    "author": item.author,
                    "fans": item.fans,
                    "likes": item.likes,
                    "collects": item.collects,
                    "comments": item.comments,
                },
            }
            for sample, item in rows
        ]


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
