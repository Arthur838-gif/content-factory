"""Alembic 迁移环境：目标元数据 = app.models.Base。

数据库地址解析顺序（前者优先）：
1. 程序化调用传入的 engine（app.db.migrate_db 经 config.attributes 传入，
   复用进程内已开 PRAGMA 的连接池）；
2. CLI `-x db=<路径>`（alembic -x db=data/app.db upgrade head）；
3. alembic.ini 的 sqlalchemy.url；
4. app.config.DB_PATH（进程环境 / .env）。

render_as_batch=True：SQLite 的 ALTER 只能整表重建，批处理模式是
后续加列/改列迁移的前置条件。
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

# 从任意 CWD（含仓库根）运行 CLI 时也能导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    if x.get("db"):
        return f"sqlite:///{Path(x['db']).resolve()}"
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app import config as app_config

    return f"sqlite:///{Path(app_config.DB_PATH).resolve()}"


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("engine")
    if connectable is None:
        connectable = create_engine(
            _db_url(), connect_args={"check_same_thread": False, "timeout": 30}
        )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
