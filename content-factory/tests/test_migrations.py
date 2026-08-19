"""Alembic 迁移机制测试：空库升级 / 旧库 stamp 基线 + 增量 / 幂等 / CLI 链路。

旧库构造方式：只升级到 0001_baseline（11 张业务表、无 alembic_version 时代等价
的 schema）后手动删掉 alembic_version，再模拟 create_all 时代的库。
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app import db as db_module
from app.db import BASELINE_REVISION, backup_before_migrate, init_db, migrate_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory(str(PROJECT_ROOT / "migrations")).get_current_head()


def _current_revision(db_path: Path) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_empty_db_upgrades_to_head(isolated_env):
    db_path, _ = isolated_env
    head = _head_revision()
    assert _current_revision(db_path) == head
    # P-2 三表 + 基线业务表全部就位
    tables = _table_names(db_path)
    assert {"domains", "domain_keywords", "sampling_jobs"} <= tables
    assert {"topics", "articles", "hot_items", "pillars"} <= tables
    # 再跑一次幂等
    assert migrate_db(db_module.get_engine(db_path), db_path) == head


def test_legacy_db_stamps_baseline_then_upgrades(tmp_path, monkeypatch):
    """create_all 时代的库（有 11 张业务表、无 alembic_version）：stamp + 增量。"""
    from app import config

    db_path = tmp_path / "legacy.db"
    backups = tmp_path / "backups"
    monkeypatch.setattr(config, "BACKUP_DIR", backups)
    engine = db_module.get_engine(db_path)

    # 只建基线 schema，然后抹掉版本号 → 等价 create_all 时代的库
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["engine"] = engine
    command.upgrade(cfg, BASELINE_REVISION)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE alembic_version")
        conn.execute(
            "INSERT INTO topics (title, angle, domain, source, status, score, created_at) "
            "VALUES ('旧库既有选题', '', '', 'radar', 'new', 1.0, '2026-01-01 00:00:00')"
        )

    migrate_db(engine, db_path)
    assert _current_revision(db_path) == _head_revision()
    # 升级前自动备份（pre_migrate_*.db，不参与每日备份轮换）
    assert backups.exists() and list(backups.glob("pre_migrate_*.db"))
    # 历史数据原样保留
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 1
    assert {"domains", "domain_keywords", "sampling_jobs"} <= _table_names(db_path)


def test_behind_db_upgrades_with_backup(isolated_env):
    """已纳管但落后（停在 baseline）→ 备份 + 增量到 head。"""
    db_path, _ = isolated_env
    engine = db_module.get_engine(db_path)
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["engine"] = engine
    command.downgrade(cfg, BASELINE_REVISION)
    assert _current_revision(db_path) == BASELINE_REVISION

    migrate_db(engine, db_path)
    assert _current_revision(db_path) == _head_revision()
    assert list((db_path.parent.parent / "backups").glob("pre_migrate_*.db"))


def test_head_db_is_noop_no_backup(isolated_env):
    """已在 head：幂等直返，不产生新备份。"""
    db_path, _ = isolated_env
    backups = db_path.parent.parent / "backups"
    before = list(backups.glob("pre_migrate_*.db")) if backups.exists() else []
    assert migrate_db(db_module.get_engine(db_path), db_path) == _head_revision()
    assert list(backups.glob("pre_migrate_*.db")) == before


def test_backup_survives_wal_activity(isolated_env):
    """活跃 WAL 库（有未合并的 -wal 文件）也能安全备份（SQLite backup API）。"""
    db_path, _ = isolated_env
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO domains (name, type, enabled, ordering, created_at, updated_at) "
            "VALUES ('测试领域', 'custom', 1, 10, '2026-01-01', '2026-01-01')"
        )
    dest = backup_before_migrate(db_path)
    assert dest is not None and dest.exists()
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 1


def test_cli_upgrade_head(tmp_path, monkeypatch):
    """CLI 链路：python -m alembic -x db=... upgrade head（CI 用同一路径）。"""
    monkeypatch.chdir(PROJECT_ROOT)
    db_path = tmp_path / "cli.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db_path}", "upgrade", "head"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert _current_revision(db_path) == _head_revision()


def test_init_db_idempotent(isolated_env):
    db_path, _ = isolated_env
    init_db(db_path)
    init_db(db_path)
    assert _current_revision(db_path) == _head_revision()
