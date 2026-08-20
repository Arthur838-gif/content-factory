"""采样任务接口（P-2）：入队（202 + job_id）/ 查询 / 取消 / 重试。

付费网络请求一律不在 API 进程里执行——入队只写一行 queued，
由 worker（内嵌线程或独立进程）领取执行；页面按 job_id 轮询真实进度。
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import config
from ..collectors.base import circuit_open
from ..services import sampling_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sampling", tags=["sampling"])

# 可上队列的关键词型采样器（P9 起双采样器）；其它采集器走同步接口
SAMPLING_COLLECTORS = ("xhs_sample", "gzh_sample")


class JobIn(BaseModel):
    collector: str = Field("xhs_sample", description="采样器名（支持 xhs_sample / gzh_sample）")
    keywords: list[str] | None = Field(None, description="显式关键词；缺省按词池/词表推导")


@router.post("/jobs", status_code=202)
def create_job(body: JobIn) -> dict:
    """入队一次采样（手动触发入口）。熔断时 409（与同步触发口径一致）。

    同一采集器已有活跃手动任务时不重复入队（防连点重复计费），
    返回在跑任务并标记 deduplicated。
    """
    if body.collector not in SAMPLING_COLLECTORS:
        raise HTTPException(
            422, f"采样任务暂只支持 {' / '.join(SAMPLING_COLLECTORS)}（收到 {body.collector}）"
        )
    if circuit_open(body.collector):
        raise HTTPException(
            409,
            f"采集器 {body.collector} 已熔断；POST /api/admin/collectors/"
            f"{body.collector}/resume 显式恢复后再触发",
        )
    try:
        job, created = sampling_jobs.enqueue(
            kind="manual", collector=body.collector, keywords=body.keywords
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"job": sampling_jobs.job_dict(job), "created": created}


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100), status: str | None = None) -> dict:
    """最近任务列表（id 倒序）。status 可过滤。"""
    if status and status not in sampling_jobs.ACTIVE_STATUSES + sampling_jobs.TERMINAL_STATUSES:
        raise HTTPException(422, f"未知状态 {status}")
    jobs = sampling_jobs.list_jobs(limit=limit, status=status)
    return {"jobs": [sampling_jobs.job_dict(j) for j in jobs]}


@router.get("/jobs/{job_id}")
def job_detail(job_id: int) -> dict:
    job = sampling_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return sampling_jobs.job_dict(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    """取消排队中的任务；运行中任务不抢跑（等它跑完或失败后重试）。"""
    ok, reason = sampling_jobs.cancel_job(job_id)
    if not ok:
        if reason == "not_found":
            raise HTTPException(404, f"任务 {job_id} 不存在")
        if reason == "running":
            raise HTTPException(409, "任务正在运行，无法取消（完成后可在详情页重试）")
        if reason == "gone":
            raise HTTPException(409, "任务刚被 worker 领走，无法取消")
        raise HTTPException(409, f"任务已是终态（{reason}），无需取消")
    job = sampling_jobs.get_job(job_id)
    return sampling_jobs.job_dict(job)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict:
    """重试终态任务：回排队，保留已完成进度（续跑剩余关键词）。"""
    job, outcome = sampling_jobs.retry_job(job_id)
    if outcome == "not_found":
        raise HTTPException(404, f"任务 {job_id} 不存在")
    if outcome == "active":
        state = "排队中" if job.status == "queued" else "运行中"
        raise HTTPException(409, f"任务仍在{state}，无需重试")
    if outcome == "conflict":
        raise HTTPException(
            409,
            f"已有新的活跃任务占用同一去重键（{job.dedupe_key}），等它完成后再重试",
        )
    return sampling_jobs.job_dict(job)


@router.get("/status")
def worker_status() -> dict:
    """采样队列概览（/viral 页与工作台展示用）。"""
    jobs = sampling_jobs.list_jobs(limit=50)
    active = [j for j in jobs if j.status in sampling_jobs.ACTIVE_STATUSES]
    recent = [j for j in jobs if j.status in sampling_jobs.TERMINAL_STATUSES][:5]
    latest = active[0] if active else (recent[0] if recent else None)
    return {
        "embedded_worker": bool(config.WORKER_EMBEDDED),
        "active_count": len(active),
        "latest": sampling_jobs.job_dict(latest) if latest else None,
        "recent": [sampling_jobs.job_dict(j) for j in recent],
    }
