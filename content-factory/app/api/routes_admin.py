"""管理接口（P-1a 起）：手动触发采集（计划书第 8 章）；P-1b 增熔断状态与人工恢复。"""
import logging

from fastapi import APIRouter, HTTPException

from ..collectors import base as collectors_base
from ..collectors import hotboard  # noqa: F401  注册 hotboard 采集器
from ..collectors import xhs_sample  # noqa: F401  注册 xhs_sample 采样器（P-1b）
from ..collectors import github_tools  # noqa: F401  注册 github_tools 采集器（P7）
from ..services import radar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["admin"])

# 手动触发的运维任务统一登记进 collectors.base._MANUAL_TASKS（伪采集器：
# 非 fetch() 协议、不参与熔断，仅调试/演练用）；此处只做登记，不建平行注册表
collectors_base.register_manual_task("xhs_teardown", radar.run_weekly_teardown)


@router.post("/collectors/{name}/run")
def trigger_collector(name: str):
    """手动触发一次采集（调试用）。name: hotboard / xhs_sample / xhs_teardown。

    409 = 采集器已熔断（需人工恢复）；502 = mcp 等上游失败（失败计数 +1）。
    502 detail 只回类型化摘要，不回 repr(exc)——异常文本可能带内部路径/地址。
    """
    task = collectors_base.get_manual_task(name)
    if task is not None:
        try:
            summary = task()
        except Exception as exc:
            logger.exception("手动任务 %s 失败", name)
            raise HTTPException(
                status_code=502, detail=f"任务失败（{type(exc).__name__}）：{str(exc)[:200]}"
            ) from exc
        return {"collector": name, **summary}
    try:
        return collectors_base.run_collector(name)
    except collectors_base.UnknownCollectorError:
        available = "、".join(
            collectors_base.available_collectors() + collectors_base.available_tasks()
        )
        raise HTTPException(
            status_code=404,
            detail=f"未知采集器 {name}；当前可用：{available}",
        ) from None
    except collectors_base.CircuitOpenError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc}；POST /api/admin/collectors/{name}/resume 显式恢复",
        ) from None
    except Exception as exc:
        logger.exception("手动采集 %s 失败", name)
        raise HTTPException(
            status_code=502, detail=f"采集失败（{type(exc).__name__}）：{str(exc)[:200]}"
        ) from exc


@router.get("/admin/collectors")
def list_collector_status() -> dict:
    """采集器状态一览（含熔断状态与连续失败计数）。"""
    states = collectors_base.collector_status()
    return {
        "collectors": [
            {"name": name, "status": "idle", "consecutive_failures": 0}
            for name in collectors_base.available_collectors()
            if name not in {s["name"] for s in states}
        ]
        + states
    }


@router.post("/admin/collectors/{name}/resume")
def resume_collector(name: str) -> dict:
    """人工恢复熔断的采集器：失败计数清零。仅熔断状态可恢复（否则 409）。"""
    if name not in collectors_base.available_collectors():
        raise HTTPException(status_code=404, detail=f"未知采集器 {name}")
    if not collectors_base.resume_collector(name):
        raise HTTPException(status_code=409, detail=f"采集器 {name} 未处于熔断状态，无需恢复")
    logger.info("采集器 %s 已人工恢复，失败计数清零", name)
    return {"name": name, "status": "enabled", "consecutive_failures": 0}
