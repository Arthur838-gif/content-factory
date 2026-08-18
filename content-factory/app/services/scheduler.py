"""APScheduler 任务注册（计划书 6.2 / 第 5 章数据保留 / 第 10 章备份纪律）。

任务一览：
  hotboard   每小时采集一次热榜（M1）
  expire     每小时把到期且仍为 new 的选题置为 archived（第 5 章）
  xhs_sample 每 6 小时小红书只读采样（M2，P-1b；熔断后自动跳过）
  teardown   每周一 06:00 低粉爆款周度 LLM 拆解（A3，P-1b）
  backup     每日 03:00 备份 SQLite → data/backups/app_YYYYMMDD.db，保留 7 份
  cleanup    每周日 05:00 物理删除 90 天前的 hot_items（与备份错开 ≥ 1 小时）
"""
import logging
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from .. import config
from ..collectors import base as collectors_base
from ..collectors import hotboard  # noqa: F401  注册 hotboard 采集器
from ..collectors import xhs_sample  # noqa: F401  注册 xhs_sample 采样器（P-1b）
from ..collectors import github_tools  # noqa: F401  注册 github_tools 采集器（P7）
from ..db import session_scope
from . import notify, radar

logger = logging.getLogger(__name__)


def backup_database() -> Path:
    """SQLite 单文件备份：走 sqlite3 backup API（WAL 安全），保留最近 7 份。"""
    src = Path(config.DB_PATH)
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在：{src}")
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.BACKUP_DIR / f"app_{datetime.now():%Y%m%d}.db"

    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    backups = sorted(p for p in config.BACKUP_DIR.glob("app_*.db") if p.is_file())
    for stale in backups[: max(0, len(backups) - config.BACKUP_KEEP)]:
        stale.unlink()
        logger.info("清理过期备份：%s", stale.name)
    logger.info("备份完成：%s（共 %s 份）", dest, min(len(backups) + 1, config.BACKUP_KEEP))
    return dest


def _scheduled_job(module: str, event: str):
    """定时任务统一包装：异常记日志（含堆栈）并外发告警，绝不让任务异常逃出调度器。

    CircuitOpenError 由 job 体自己先接住（那是预期内的跳过，只 warning 不告警）。
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                logger.exception("%s 任务异常", fn.__name__)
                notify.send_alert("ERROR", module, event, repr(exc))

        return wrapper

    return deco


@_scheduled_job("hotboard", "定时采集失败")
def job_hotboard() -> None:
    try:
        collectors_base.run_collector("hotboard")
    except collectors_base.CircuitOpenError as exc:
        logger.warning("热榜采集已熔断，跳过本轮：%s", exc)


@_scheduled_job("xhs_sample", "定时采样失败")
def job_xhs_sample() -> None:
    try:
        collectors_base.run_collector("xhs_sample")
    except collectors_base.CircuitOpenError as exc:
        logger.warning("小红书采样已熔断，跳过本轮（等待人工恢复）：%s", exc)


@_scheduled_job("xhs_teardown", "周度拆解任务异常")
def job_xhs_teardown() -> None:
    radar.run_weekly_teardown()


@_scheduled_job("scheduler", "选题过期归档任务异常")
def job_expire_topics() -> None:
    with session_scope() as session:
        archived = radar.archive_expired_topics(session)
    if archived:
        logger.info("过期选题归档 %s 条", archived)


@_scheduled_job("backup", "每日备份失败")
def job_backup() -> None:
    backup_database()


@_scheduled_job("cleanup", "数据清理任务异常")
def job_cleanup() -> None:
    with session_scope() as session:
        removed = radar.cleanup_hot_items(session)
    if removed:
        logger.info("清理 90 天前 hot_items %s 条", removed)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=None)  # 跟随本地时区
    scheduler.add_job(
        job_hotboard, "interval", hours=1, id="hotboard",
        next_run_time=datetime.now(), coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        job_expire_topics, "interval", hours=1, id="expire_topics",
        coalesce=True, max_instances=1,
    )
    # xhs 采样不随启动立即执行（mcp 未部署时避免启动即连败熔断）；熔断后由 job 内跳过
    scheduler.add_job(
        job_xhs_sample, "interval", hours=config.XHS_SAMPLE_INTERVAL_HOURS, id="xhs_sample",
        coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        job_xhs_teardown, "cron", day_of_week=config.XHS_TEARDOWN_WEEKDAY,
        hour=config.XHS_TEARDOWN_HOUR, minute=0, id="xhs_teardown", coalesce=True,
    )
    scheduler.add_job(job_backup, "cron", hour=3, minute=0, id="backup", coalesce=True)
    scheduler.add_job(
        job_cleanup, "cron", day_of_week="sun", hour=5, minute=0, id="cleanup", coalesce=True
    )
    return scheduler
