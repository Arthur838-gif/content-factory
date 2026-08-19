"""SQLAlchemy 引擎与会话。SQLite 单文件，外键约束与 WAL 在连接层打开。

P-2 起 schema 变更一律走 Alembic（migrations/），init_db 做迁移引导：
空库从零升级到最新；create_all 时代的旧库先备份、stamp 基线再增量升级。
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base

logger = logging.getLogger(__name__)

_engine_cache: dict[str, object] = {}

# 基线 = 计划书 v1.3 第 5 章的 11 张表（create_all 时代的既有库都停在这个版本）
BASELINE_REVISION = "0001_baseline"

# 既有库里必须存在的基线表（缺表说明库文件不是本应用的，stamp 前给出警告）
_LEGACY_TABLES = frozenset(
    {
        "topics", "prompts", "articles", "assets", "hot_items", "viral_samples",
        "tag_library", "publish_records", "collector_state", "pillars", "week_themes",
    }
)


def get_engine(db_path: str | Path | None = None):
    path = Path(db_path or config.DB_PATH)
    key = str(path.resolve())
    if key not in _engine_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{path}",
            # timeout = SQLite busy timeout（秒）：调度线程与 API 并发写时，
            # 写锁竞争短暂等待重试而不是立刻抛 "database is locked"
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine, "connect")
        def _set_pragma(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        _engine_cache[key] = engine
    return _engine_cache[key]


def get_session_factory(db_path: str | Path | None = None) -> sessionmaker:
    return sessionmaker(bind=get_engine(db_path), expire_on_commit=False)


@contextmanager
def session_scope(db_path: str | Path | None = None):
    """一次采集/任务一个事务：正常提交，异常回滚。"""
    factory = get_session_factory(db_path)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---- Alembic 迁移引导 ----
def _alembic_config(engine) -> AlembicConfig:
    """程序化调用配置：复用进程内 engine（沿用 PRAGMA/连接池），不走 ini 文件。"""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    cfg.attributes["engine"] = engine
    return cfg


def _current_revision(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def backup_before_migrate(db_file: Path) -> Path | None:
    """升级已有库前备份一份（SQLite backup API，WAL 安全）。

    文件名刻意不用 app_ 前缀：调度器每日备份按 app_*.db 清理保留 7 份，
    迁移前备份不参与轮换、永久保留（迁移失败回滚的唯一后路）。
    """
    if not db_file.exists():
        return None
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.BACKUP_DIR / f"pre_migrate_{datetime.now():%Y%m%d_%H%M%S}.db"
    src_conn = sqlite3.connect(db_file)
    dst_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    logger.info("迁移前备份：%s", dest)
    return dest


def migrate_db(engine, db_path: str | Path | None = None) -> str:
    """迁移引导，幂等。返回应用后的版本号。

    三种情况：
    1. 空库（无表）→ 从 base 全量升级；
    2. 旧库（有表、无 alembic_version）→ 备份 + stamp 基线 + 增量升级；
    3. 已纳管但落后 → 备份 + 增量升级；已是 head → 直接返回不动库。
    """
    script = ScriptDirectory(str(Path(__file__).resolve().parent.parent / "migrations"))
    head = script.get_current_head()
    db_file = Path(db_path or config.DB_PATH)
    tables = set(inspect(engine).get_table_names())

    if not tables:
        command.upgrade(_alembic_config(engine), head)
        logger.info("空库已按迁移链建表（版本 %s）：%s", head, db_file)
        return head

    current = _current_revision(engine)
    if current is None:
        missing = _LEGACY_TABLES - tables
        if missing:
            logger.warning(
                "库中缺少基线表 %s，仍按 %s stamp；如非预期请核对库文件 %s",
                sorted(missing), BASELINE_REVISION, db_file,
            )
        backup_before_migrate(db_file)
        command.stamp(_alembic_config(engine), BASELINE_REVISION)
        current = BASELINE_REVISION

    if current != head:
        backup_before_migrate(db_file)
        logger.info("数据库升级：%s → %s（%s）", current, head, db_file)
        command.upgrade(_alembic_config(engine), head)
    return head


def init_db(db_path: str | Path | None = None) -> None:
    """建库建表并升级到最新迁移版本。幂等，可重复调用。"""
    engine = get_engine(db_path)
    version = migrate_db(engine, db_path)
    logger.info("数据库就绪（schema %s）：%s", version, Path(db_path or config.DB_PATH))
