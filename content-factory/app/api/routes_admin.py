"""管理接口（P-1a）：手动触发采集（计划书第 8 章）。"""
import logging

from fastapi import APIRouter, HTTPException

from ..collectors import base as collectors_base
from ..collectors import hotboard  # noqa: F401  注册 hotboard 采集器
from ..schemas import CollectorRunResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/collectors/{name}/run", response_model=CollectorRunResult)
def trigger_collector(name: str) -> CollectorRunResult:
    """手动触发一次采集（调试用）。name: hotboard（xhs_sample 属 P-1b）。"""
    try:
        result = collectors_base.run_collector(name)
    except collectors_base.UnknownCollectorError:
        available = "、".join(collectors_base.available_collectors())
        raise HTTPException(
            status_code=404,
            detail=f"未知采集器 {name}；当前可用：{available}",
        ) from None
    except Exception as exc:
        logger.exception("手动采集 %s 失败", name)
        raise HTTPException(status_code=502, detail=f"采集失败：{exc!r}") from exc
    return result
