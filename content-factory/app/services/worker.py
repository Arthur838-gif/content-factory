"""采样任务 worker（P-2）：领取 sampling_jobs 并逐关键词执行。

执行模型：
- 每个关键词一个事务：素材入库（URL 去重 + 领域过滤 + 低粉爆款判定）与
  任务进度（计数器/当前词/心跳/租约）同事务落库，崩溃最多丢当前关键词；
- RedFox 单词失败自动降级 mcp（降级词记 job.meta，不算任务失败）；
  两源全挂才判任务失败，走 collector_state 熔断计数（与同步路径同一语义）；
- 全关键词零抓取 → succeeded_empty：合法空结果，成功不熔断；
- 领取时发现熔断 → blocked（等待人工恢复后重试）。

运行方式：
- 单机开发（默认）：API 进程内嵌线程（config.WORKER_EMBEDDED）；
- 长期部署：`.venv/Scripts/python -m app.services.worker`（独立进程，可多个；
  领取是条件 UPDATE，多 worker 不会重复执行同一任务）。
"""
import argparse
import logging
import threading
from datetime import datetime, timedelta

from .. import config
from ..collectors.base import circuit_open, handle_collector_failure, record_success
from ..db import session_scope
from ..models import SamplingJob
from . import sampling_jobs

logger = logging.getLogger(__name__)

# 内嵌 worker 句柄（run_embedded_worker 写入；stop_embedded_worker 读取）
_embedded_worker: "SamplingWorker | None" = None


class SamplingWorker:
    def __init__(self, db_path=None, poll_seconds: float | None = None,
                 lease_seconds: int | None = None):
        self.db_path = db_path
        self.poll_seconds = poll_seconds or config.WORKER_POLL_SECONDS
        self.lease_seconds = lease_seconds or config.WORKER_JOB_LEASE_SECONDS
        self._stop = threading.Event()

    # ---- 对外入口 ----
    def run_once(self) -> str:
        """执行一个任务。返回终态：idle / blocked / succeeded / succeeded_empty /
        failed / error（error = worker 自身异常，任务状态已按失败兜底）。"""
        sampling_jobs.requeue_stale(db_path=self.db_path)
        job_id = sampling_jobs.claim_next(
            lease_seconds=self.lease_seconds, db_path=self.db_path
        )
        if job_id is None:
            return "idle"
        try:
            return self._execute(job_id)
        except Exception as exc:
            logger.exception("worker 执行任务 #%s 异常", job_id)
            self._fail(job_id, exc)
            return "error"

    def run_forever(self) -> None:
        logger.info("sampling worker 启动（轮询 %.1fs，租约 %ss）",
                    self.poll_seconds, self.lease_seconds)
        while not self._stop.is_set():
            try:
                outcome = self.run_once()
            except Exception:  # run_once 已兜底，这里防未知路径撕线程
                logger.exception("worker 轮询异常")
                outcome = "idle"
            if outcome == "idle":
                self._stop.wait(self.poll_seconds)
        logger.info("sampling worker 已停止")

    def stop(self) -> None:
        self._stop.set()

    # ---- 内部 ----
    def _execute(self, job_id: int) -> str:
        from ..collectors.xhs_sample import XhsSampleCollector

        with session_scope(self.db_path) as session:
            job = session.get(SamplingJob, job_id)
            if job is None or job.status != "running":
                return "idle"  # 领取后被取消等（防御，正常不会发生）
            keywords = list(job.keywords or [])
            done = job.completed_queries or 0
            attempts = job.attempts or 1
            collector_name = job.collector

        if circuit_open(collector_name, self.db_path):
            sampling_jobs.finish_job(
                job_id, "blocked", error_type="circuit_open",
                error=f"采集器 {collector_name} 已熔断；人工恢复（POST /api/admin/"
                      f"collectors/{collector_name}/resume）后可重试该任务",
                db_path=self.db_path,
            )
            logger.warning("任务 #%s 被熔断拦截（blocked）", job_id)
            return "blocked"

        if collector_name != "xhs_sample":
            # 本轮只把关键词型采样器搬上队列；其它采集器走同步接口
            sampling_jobs.finish_job(
                job_id, "failed", error_type="unsupported_collector",
                error=f"worker 暂不支持采集器 {collector_name} 的异步执行",
                db_path=self.db_path,
            )
            return "failed"

        collector = XhsSampleCollector(keywords=keywords)
        pending = keywords[done:]
        logger.info("任务 #%s 开始执行：关键词 %s 个（续跑 %s，第 %s 次尝试）",
                    job_id, len(keywords), len(pending), attempts)

        failed_exc: Exception | None = None
        for index, keyword in enumerate(pending):
            try:
                items, source = collector.fetch_keyword(keyword)
            except Exception as exc:
                failed_exc = exc
                logger.exception("任务 #%s 采样关键词 %r 失败", job_id, keyword)
                break
            # 记录命中该条的检索词：pillar 排期按标题或采样词匹配（标题未必含关键词）
            for item in items:
                raw = dict(item.raw or {})
                raw.setdefault("keyword", keyword)
                item.raw = raw

            # 素材入库 + 进度落库同一事务：崩溃时两者要么都在要么都不在
            now = datetime.now()
            with session_scope(self.db_path) as session:
                from ..collectors.base import persist_hot_items

                result = persist_hot_items(session, items, collector=collector_name)
                row = session.get(SamplingJob, job_id)
                if row is None or row.status != "running":
                    # 执行中被取消/回收：已入库素材保留（URL 去重兜底），任务不再推进
                    logger.warning("任务 #%s 执行中状态变为 %s，停止推进",
                                   job_id, row.status if row else "deleted")
                    return "canceled"
                row.fetched += result.fetched
                row.inserted += result.inserted
                row.filtered_out += result.filtered_out
                row.duplicates_skipped += result.duplicates_skipped
                row.topics_created += result.topics_created
                row.topics_merged += result.topics_merged
                row.viral_created += result.viral_created
                row.completed_queries = done + index + 1
                next_pos = done + index + 1
                row.current_keyword = keywords[next_pos] if next_pos < len(keywords) else None
                row.heartbeat = now
                row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                # JSON 列必须整体换新容器，原地改写 flush 时不会发 UPDATE
                meta = dict(row.meta or {})
                sources = dict(meta.get("sources") or {})
                sources[keyword] = source
                meta["sources"] = sources
                if source == "mcp":
                    degraded = list(meta.get("degraded_keywords") or [])
                    degraded.append(keyword)
                    meta["degraded_keywords"] = degraded
                row.meta = meta
                logger.info(
                    "任务 #%s 进度 %s/%s（%s：%s 抓取 / %s 入库）",
                    job_id, row.completed_queries, row.total_queries, keyword,
                    result.fetched, result.inserted,
                )

        if failed_exc is not None:
            handle_collector_failure(collector_name, failed_exc)
            self._fail(job_id, failed_exc)
            return "failed"

        record_success(collector_name)
        with session_scope(self.db_path) as session:
            row = session.get(SamplingJob, job_id)
            status = "succeeded_empty" if (row.fetched or 0) == 0 else "succeeded"
        sampling_jobs.finish_job(job_id, status, db_path=self.db_path)
        logger.info("任务 #%s 完成：%s", job_id, status)
        return status

    def _fail(self, job_id: int, exc: Exception) -> None:
        sampling_jobs.finish_job(
            job_id, "failed", error_type=type(exc).__name__,
            error=str(exc), db_path=self.db_path,
        )


def run_embedded_worker(db_path=None, poll_seconds: float | None = None) -> threading.Thread:
    """API 进程内嵌 worker 线程（daemon：随进程退出，无需优雅停机）。

    句柄存模块级 _embedded_worker：lifespan 停机与测试收尾调
    stop_embedded_worker() 立即停止轮询，避免线程摸已释放的临时库。
    """
    global _embedded_worker
    worker = SamplingWorker(db_path=db_path, poll_seconds=poll_seconds)
    _embedded_worker = worker
    thread = threading.Thread(target=worker.run_forever, name="sampling-worker", daemon=True)
    thread.start()
    return thread


def stop_embedded_worker() -> bool:
    """停止内嵌 worker（未启动返回 False）。线程本身 daemon，不等待退出。"""
    global _embedded_worker
    if _embedded_worker is None:
        return False
    _embedded_worker.stop()
    _embedded_worker = None
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.worker",
        description="sampling worker：领取并执行采样任务（Ctrl+C 退出）",
    )
    parser.add_argument("--once", action="store_true", help="只执行一个任务就退出（调试/补跑）")
    parser.add_argument("--interval", type=float, default=None, help="轮询间隔秒数")
    args = parser.parse_args(argv)

    worker = SamplingWorker(poll_seconds=args.interval)
    if args.once:
        outcome = worker.run_once()
        print(f"任务结果：{outcome}")
        return 0 if outcome in ("succeeded", "succeeded_empty", "idle", "blocked") else 1
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
