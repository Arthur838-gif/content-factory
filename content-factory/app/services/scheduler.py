"""APScheduler 任务注册（计划书 6.2 / 第 5 章数据保留 / 第 10 章备份纪律）。

任务一览：
  hotboard   每小时采集一次热榜（M1）
  expire     每小时把到期且仍为 new 的选题置为 archived（第 5 章）
  backup     每日 03:00 备份 SQLite → data/backups/app_YYYYMMDD.db，保留 7 份
  cleanup    每周日 05:00 物理删除 90 天前的 hot_items（与备份错开 ≥ 1 小时）
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from .. import config
from ..collectors import base as collectors_base
from ..collectors import hotboard  # noqa: F401  注册 hotboard 采集器
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


def job_hotboard() -> None:
    try:
        collectors_base.run_collector("hotboard")
    except Exception as exc:
        logger.exception("定时热榜采集失败")
        notify.send_alert("ERROR", "hotboard", "定时采集失败", repr(exc))


def job_expire_topics() -> None:
    try:
        with session_scope() as session:
            archived = radar.archive_expired_topics(session)
        if archived:
            logger.info("过期选题归档 %s 条", archived)
    except Exception as exc:
        logger.exception("选题过期归档任务异常")
        notify.send_alert("ERROR", "scheduler", "选题过期归档任务异常", repr(exc))


def job_backup() -> None:
    try:
        backup_database()
    except Exception as exc:
        logger.exception("每日备份失败")
        notify.send_alert("ERROR", "backup", "每日备份失败", repr(exc))


def job_cleanup() -> None:
    try:
        with session_scope() as session:
            removed = radar.cleanup_hot_items(session)
        if removed:
            logger.info("清理 90 天前 hot_items %s 条", removed)
    except Exception as exc:
        logger.exception("数据清理任务异常")
        notify.send_alert("ERROR", "cleanup", "数据清理任务异常", repr(exc))


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
    scheduler.add_job(job_backup, "cron", hour=3, minute=0, id="backup", coalesce=True)
    scheduler.add_job(
        job_cleanup, "cron", day_of_week="sun", hour=5, minute=0, id="cleanup", coalesce=True
    )
    return scheduler
