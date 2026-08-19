"""领域发现接口（P5d）：建栏目表单的类目 / 推荐词 / 对标账号。

三个端点都只读；推荐词与对标账号各自消耗 1 次 RedFox 调用（按调用计费），
由页面按钮手动触发，服务层带 6 小时缓存。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from ..collectors.redfox import RedFoxError, XHS_CATEGORIES
from ..services import domain_service, xhs_discovery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/categories")
def categories() -> dict:
    """官方类目（七日爆款枚举）+ 库里的自定义领域，供表单下拉分组。"""
    custom = [d for d in domain_service.load_domains() if d not in XHS_CATEGORIES]
    return {"official": XHS_CATEGORIES, "custom": custom}


@router.get("/keyword-ideas")
def keyword_ideas(category: str = Query(min_length=1)) -> dict:
    """类目七日爆款 → 推荐关键词（hashtag 词频）+ 爆款标题示例。"""
    if not xhs_discovery.discovery_ready():
        raise HTTPException(503, "未配置 REDFOX_API_KEY，无法获取推荐词（词表关键词仍可手动填）")
    try:
        return xhs_discovery.category_insights(category)
    except RedFoxError as exc:
        raise HTTPException(502, f"获取推荐词失败：{exc}") from exc


@router.get("/benchmark-accounts")
def benchmark_accounts(keyword: str = Query(min_length=1)) -> dict:
    """按关键词搜对标账号（最热优先，前 8 个）。"""
    if not xhs_discovery.discovery_ready():
        raise HTTPException(503, "未配置 REDFOX_API_KEY，无法搜索对标账号")
    try:
        return {"keyword": keyword, "accounts": xhs_discovery.benchmark_accounts(keyword)}
    except RedFoxError as exc:
        raise HTTPException(502, f"搜索对标账号失败：{exc}") from exc
