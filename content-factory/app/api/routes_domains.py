"""领域管理接口（P-2）：词表入库后的管理面（供后续后台管理页使用）。

只覆盖本轮约定：列表、创建/合并、追加关键词、启用/停用。
关键词的编辑与删除暂不开放（防止误删导致采集过滤失配，后续按需加）。
"""
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from ..db import session_scope
from ..services import domain_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/domains", tags=["domains"])


class DomainIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    keywords: list[Annotated[str, StringConstraints(max_length=64, strip_whitespace=True)]] = []


class KeywordsIn(BaseModel):
    keywords: list[Annotated[str, StringConstraints(max_length=64, strip_whitespace=True)]] = Field(min_length=1)


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
def list_all(include_disabled: bool = True, limit: int = Query(200, ge=1, le=500)) -> list[dict]:
    """领域清单（ordering 升序），含关键词与统计。"""
    rows = domain_service.list_domains(include_disabled=include_disabled)
    return rows[:limit]


@router.post("", status_code=201)
def create_domain(body: DomainIn) -> dict:
    """创建/合并领域（已有领域只追加缺失关键词，匹配优先级不变）。"""
    with session_scope() as session:
        result = domain_service.upsert_domain(session, body.name, body.keywords, source="user")
    if result["created"]:
        logger.info("领域创建：%s（关键词 %s 个）", body.name, len(body.keywords))
    elif result["added_keywords"]:
        logger.info("领域合并：%s 追加关键词 %s", body.name, result["added_keywords"])
    return result


@router.post("/{name}/keywords", status_code=201)
def append_keywords(name: str, body: KeywordsIn) -> dict:
    """向已有领域追加关键词（不存在的领域按自定义领域创建）。"""
    with session_scope() as session:
        result = domain_service.upsert_domain(session, name, body.keywords, source="user")
    return result


@router.put("/{name}/enabled")
def set_enabled(name: str, body: EnabledIn) -> dict:
    """启用/停用领域：停用后不参与采集匹配与采样词兜底，历史数据不动。"""
    if not domain_service.set_domain_enabled(name, body.enabled):
        raise HTTPException(404, f"领域 {name} 不存在")
    logger.info("领域 %s %s", name, "启用" if body.enabled else "停用")
    return {"name": name, "enabled": body.enabled}
