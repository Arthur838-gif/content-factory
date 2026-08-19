"""采样任务服务（P-2）：持久化队列 + 原子领取 + 可恢复执行的数据层。

职责边界：
- API/调度器只调 enqueue() 入队（事务内插入即返回，不做任何网络请求）；
- worker（app.services.worker）claim_next() 原子领取后逐关键词执行，
  每个关键词一个事务：素材入库与任务进度同事务落库，崩溃最多丢当前词；
- collector_state 仍只管熔断（连续失败计数），不混入任务进度；
- RedFox 失败但 mcp 降级成功 → 记进 job.meta.degraded_keywords，不算失败；
- 全关键词零抓取 → succeeded_empty（合法结果，不触发熔断）。

状态机：queued → running → succeeded / succeeded_empty / failed / blocked / canceled
（blocked = 领取时发现熔断；lease 过期的 running 由 requeue_stale 回队或判失败）。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import config
from ..db import session_scope
from ..models import SamplingJob

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "succeeded_empty", "failed", "blocked", "canceled")


def _now() -> datetime:
    return datetime.now()


def job_dict(job: SamplingJob) -> dict:
    """任务 API 视图（时间统一 ISO 秒）。"""
    return {
        "id": job.id,
        "kind": job.kind,
        "collector": job.collector,
        "pillar_id": job.pillar_id,
        "status": job.status,
        "keywords": job.keywords or [],
        "total_queries": job.total_queries,
        "completed_queries": job.completed_queries,
        "current_keyword": job.current_keyword,
        "fetched": job.fetched,
        "inserted": job.inserted,
        "filtered_out": job.filtered_out,
        "duplicates_skipped": job.duplicates_skipped,
        "topics_created": job.topics_created,
        "topics_merged": job.topics_merged,
        "viral_created": job.viral_created,
        "requested_at": job.requested_at.isoformat(timespec="seconds") if job.requested_at else None,
        "started_at": job.started_at.isoformat(timespec="seconds") if job.started_at else None,
        "finished_at": job.finished_at.isoformat(timespec="seconds") if job.finished_at else None,
        "heartbeat": job.heartbeat.isoformat(timespec="seconds") if job.heartbeat else None,
        "attempts": job.attempts,
        "error_type": job.error_type,
        "error": job.error,
        "meta": job.meta or {},
    }


def _default_dedupe_key(kind: str, collector: str, pillar_id: int | None) -> str:
    if kind == "pillar":
        return f"pillar:{pillar_id}"
    return f"{kind}:{collector}"


def resolve_default_keywords() -> list[str]:
    """无显式关键词时的推导（与 XhsSampleCollector._queries 同口径）：
    环境变量 > 启用栏目关键词池 > 领域词表。"""
    from ..collectors.xhs_sample import XhsSampleCollector

    return XhsSampleCollector()._queries()


def enqueue(
    kind: str,
    collector: str = "xhs_sample",
    keywords: list[str] | None = None,
    pillar_id: int | None = None,
    dedupe_key: str | None = None,
    db_path=None,
) -> tuple[SamplingJob, bool]:
    """入队（事务内插入，立即返回）。返回 (job, 是否新建)。

    同一 dedupe_key 已有活跃任务时不重复入队，直接返回在跑的那个
    （防止页面连点 / 调度重叠导致重复付费调用）。关键词在入队时定死快照，
    重试不漂移。无可采关键词抛 ValueError（调用方 422）。
    """
    if keywords is None:
        keywords = resolve_default_keywords()
    clean = [kw for kw in (keywords or []) if kw and str(kw).strip()]
    clean = [str(kw).strip() for kw in clean][: config.XHS_SAMPLE_MAX_QUERIES]
    if not clean:
        raise ValueError("无可采样的关键词（栏目词池与领域词表均为空，或显式列表为空）")
    key = dedupe_key or _default_dedupe_key(kind, collector, pillar_id)

    try:
        with session_scope(db_path) as session:
            job = SamplingJob(
                kind=kind,
                collector=collector,
                pillar_id=pillar_id,
                status="queued",
                keywords=clean,
                total_queries=len(clean),
                dedupe_key=key,
                requested_at=_now(),
            )
            session.add(job)
            session.flush()
            logger.info(
                "采样任务入队：job=%s kind=%s collector=%s 关键词 %s 个（dedupe=%s）",
                job.id, kind, collector, len(clean), key,
            )
            return job, True
    except IntegrityError:
        # 活跃任务撞 dedupe_key（部分唯一索引）：返回已在跑的任务
        with session_scope(db_path) as session:
            existing = session.scalars(
                select(SamplingJob).where(
                    SamplingJob.dedupe_key == key,
                    SamplingJob.status.in_(ACTIVE_STATUSES),
                )
            ).first()
            if existing is None:
                raise  # 撞的不是活跃约束（理论不可达），让上层看到原始异常
            logger.info("采样任务去重：dedupe=%s 已有活跃 job=%s，复用", key, existing.id)
            return existing, False


def claim_next(lease_seconds: int | None = None, db_path=None) -> int | None:
    """原子领取最早排队的任务：UPDATE ... WHERE status='queued' 抢锁。

    返回 job id（抢输/无任务返回 None）。attempts+1、起租约，
    current_keyword 指向第一个未完成的关键词。
    """
    lease_seconds = lease_seconds or config.WORKER_JOB_LEASE_SECONDS
    now = _now()
    with session_scope(db_path) as session:
        job = session.scalars(
            select(SamplingJob).where(SamplingJob.status == "queued").order_by(SamplingJob.id)
        ).first()
        if job is None:
            return None
        # 续跑场景：已完成 completed 个关键词，从下一个开始
        keywords = job.keywords or []
        done = job.completed_queries or 0
        next_keyword = keywords[done] if done < len(keywords) else None
        claimed = session.execute(
            update(SamplingJob)
            .where(SamplingJob.id == job.id, SamplingJob.status == "queued")
            .values(
                status="running",
                started_at=now,
                heartbeat=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempts=(job.attempts or 0) + 1,
                current_keyword=next_keyword,
                error=None,
                error_type=None,
            )
        )
        if (claimed.rowcount or 0) != 1:
            return None  # 另一个 worker 抢先了
        session.flush()
        return job.id


def requeue_stale(db_path=None, now: datetime | None = None) -> int:
    """回收超租约的 running 任务（worker 进程崩溃的兜底）。

    attempts 未用尽 → 回 queued（保留已完成进度，续跑剩余关键词；
    dedupe_key 不动，回队后仍是活跃任务）；用尽 → 判 failed。
    返回回收数。
    """
    now = now or _now()
    requeued = failed = 0
    with session_scope(db_path) as session:
        stale = session.scalars(
            select(SamplingJob).where(
                SamplingJob.status == "running",
                SamplingJob.lease_expires_at.is_not(None),
                SamplingJob.lease_expires_at < now,
            )
        ).all()
        for job in stale:
            if (job.attempts or 0) >= config.WORKER_JOB_MAX_ATTEMPTS:
                job.status = "failed"
                job.error_type = "lease_expired"
                job.error = (
                    f"任务心跳超时且尝试次数用尽（attempts={job.attempts}），"
                    "worker 可能中途崩溃；可手动重试"
                )
                job.finished_at = now
                job.current_keyword = None
                failed += 1
                logger.warning("任务 #%s 租约超时且用尽尝试次数，判失败", job.id)
            else:
                job.status = "queued"
                job.heartbeat = None
                job.lease_expires_at = None
                job.current_keyword = None
                job.error_type = "lease_expired"
                job.error = f"worker 心跳超时（第 {job.attempts} 次尝试中断），自动回队续跑"
                requeued += 1
                logger.warning("任务 #%s 租约超时，回队续跑（已完成 %s/%s）",
                               job.id, job.completed_queries, job.total_queries)
    return requeued + failed


def get_job(job_id: int, db_path=None) -> SamplingJob | None:
    with session_scope(db_path) as session:
        return session.get(SamplingJob, job_id)


def list_jobs(limit: int = 20, status: str | None = None, db_path=None) -> list[SamplingJob]:
    with session_scope(db_path) as session:
        stmt = select(SamplingJob).order_by(SamplingJob.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(SamplingJob.status == status)
        return list(session.scalars(stmt).all())


def finish_job(
    job_id: int,
    status: str,
    error_type: str | None = None,
    error: str | None = None,
    db_path=None,
) -> bool:
    """任务置终态（幂等：仅 running 可终结）。"""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"非法终态：{status}")
    now = _now()
    with session_scope(db_path) as session:
        result = session.execute(
            update(SamplingJob)
            .where(SamplingJob.id == job_id, SamplingJob.status == "running")
            .values(status=status, finished_at=now, current_keyword=None,
                    error_type=error_type, error=error[:2000] if error else None)
        )
        return (result.rowcount or 0) == 1


def cancel_job(job_id: int, db_path=None) -> tuple[bool, str]:
    """取消任务。仅 queued 可取消；running 不抢跑（返回原因由调用方转 409）。"""
    now = _now()
    with session_scope(db_path) as session:
        job = session.get(SamplingJob, job_id)
        if job is None:
            return False, "not_found"
        if job.status == "queued":
            result = session.execute(
                update(SamplingJob)
                .where(SamplingJob.id == job_id, SamplingJob.status == "queued")
                .values(status="canceled", finished_at=now, current_keyword=None)
            )
            if (result.rowcount or 0) == 1:
                logger.info("任务 #%s 已取消", job_id)
                return True, "canceled"
            return False, "gone"  # 刚被 worker 领走
        if job.status == "running":
            return False, "running"
        return False, job.status


def retry_job(job_id: int, db_path=None) -> tuple[SamplingJob | None, str]:
    """重试终态任务：回 queued，保留已完成进度（续跑剩余关键词）与关键词快照。

    仅终态可重试；该任务 dedupe_key 若已被新活跃任务占用则拒绝（409）。
    """
    now = _now()
    with session_scope(db_path) as session:
        job = session.get(SamplingJob, job_id)
        if job is None:
            return None, "not_found"
        if job.status in ACTIVE_STATUSES:
            return job, "active"
        if job.dedupe_key:
            holder = session.scalars(
                select(SamplingJob.id).where(
                    SamplingJob.dedupe_key == job.dedupe_key,
                    SamplingJob.id != job.id,
                    SamplingJob.status.in_(ACTIVE_STATUSES),
                )
            ).first()
            if holder is not None:
                return job, "conflict"
        job.status = "queued"
        job.error = None
        job.error_type = None
        job.finished_at = None
        job.heartbeat = None
        job.lease_expires_at = None
        job.current_keyword = None
        # requested_at 保留原值（排队时间线不撒谎）；attempts 保留，
        # 达上限后由 requeue_stale 兜底拒绝，主动重试不封顶
        session.flush()
        logger.info("任务 #%s 手动重试（已完成 %s/%s，续跑剩余关键词）",
                    job.id, job.completed_queries, job.total_queries)
        return job, "ok"
