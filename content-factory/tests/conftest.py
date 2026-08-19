"""pytest 配置。

两类测试共存在 tests/ 下：
- 验收脚本（test_p0/p1/p1a/p1b/p2/p3/p4/p6/pillar/discovery/github/redfox）：
  独立可执行（.venv/Scripts/python tests/test_pN.py），模块级即改 config 指向
  临时库，被 pytest 收集会互相污染——collect_ignore 排除，只经
  tests/run_all.py 或直接运行。
- 正式 pytest 用例（test_migrations / test_domains / test_sampling_jobs /
  test_lifespan）：用 isolated_env fixture 做每用例隔离（临时库 + monkeypatch
  还原 config + 引擎释放），可随意组合运行。
"""

collect_ignore = [
    "test_p0.py",
    "test_p1.py",
    "test_p1a.py",
    "test_p1b.py",
    "test_p2.py",
    "test_p3.py",
    "test_p4.py",
    "test_p6.py",
    "test_pillar.py",
    "test_discovery.py",
    "test_github.py",
    "test_redfox.py",
    "alert_receiver.py",
    "_run_real_acceptance.py",
    "verify_p1a_live.sh",
]

import pytest  # noqa: E402

from app import config, db as db_module  # noqa: E402


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """每个用例一套临时目录：DB / 备份 / 素材互不串库，config 改动用后即还原。

    返回 (db_path, data_dir)；需要领域词表的用例自己调 seed_domains（fixture
    词表 = tests/fixtures/domains.test.yml，只读共享、不复制）。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "DOMAINS_FILE",
                        db_module.Path(__file__).parent / "fixtures" / "domains.test.yml")
    # 外部依赖与开关全部关死：pytest 里不许联网、不许起线程
    monkeypatch.setattr(config, "RUN_SCHEDULER", False)
    monkeypatch.setattr(config, "WORKER_EMBEDDED", False)
    monkeypatch.setattr(config, "PILLAR_AUTO_SAMPLE", False)
    monkeypatch.setattr(config, "XHS_SAMPLE_KEYWORDS", [])
    monkeypatch.setattr(config, "NOTIFY_WEBHOOK", "")
    monkeypatch.setattr(config, "LLM_MOCK", True)
    db_module.init_db(db_path)
    yield db_path, data_dir
    # 释放引擎连接（WAL 文件随连接关闭合并），缓存里不留临时库条目
    key = str(db_path.resolve())
    engine = db_module._engine_cache.pop(key, None)
    if engine is not None:
        engine.dispose()


@pytest.fixture
def seeded_env(isolated_env):
    """isolated_env + 导入 fixture 词表（3 个自定义领域 + 24 官方类目）。"""
    from app.services import domain_service

    db_path, _ = isolated_env
    domain_service.seed_domains()
    return isolated_env


@pytest.fixture
def stub_fetch(monkeypatch):
    """打桩 XhsSampleCollector.fetch_keyword：按关键词返回预设结果，不联网。

    约定：BAD → 抛异常（双源全挂）；EMPTY → 空结果；其余 → 2 条可入库笔记
    （标题含 "AI" 命中 fixture 词表，能过领域过滤）。返回实际被调用的关键词列表。
    """
    from app.collectors import xhs_sample
    from app.schemas import HotItem as HotItemSchema

    calls: list[str] = []

    def fake_fetch(self, keyword):
        calls.append(keyword)
        if keyword == "BAD":
            raise RuntimeError("双源全挂")
        if keyword == "EMPTY":
            return [], "redfox"
        notes = [
            HotItemSchema(
                source="xhs", title=f"AI {keyword} 笔记{i}",
                url=f"https://xhs.test/{keyword}/{i}",
                fans=100, likes=1000, collects=10, comments=5,
            )
            for i in range(2)
        ]
        return notes, "redfox"

    monkeypatch.setattr(xhs_sample.XhsSampleCollector, "fetch_keyword", fake_fetch)
    return calls
