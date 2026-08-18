"""SQLAlchemy 引擎与会话。SQLite 单文件，外键约束与 WAL 在连接层打开。"""
import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base

logger = logging.getLogger(__name__)

_engine_cache: dict[str, object] = {}


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


def init_db(db_path: str | Path | None = None) -> None:
    """建库建表（第 5 章全量 8 张表）。幂等，可重复调用。"""
    Base.metadata.create_all(get_engine(db_path))
    logger.info("数据库就绪：%s", Path(db_path or config.DB_PATH))
