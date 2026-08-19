#!/usr/bin/env python
"""P7 验收脚本：GitHub 开源项目采集 → 合集真实工具素材（可重复运行，不联网）。

覆盖：
  1. to_hot_items 映射：star 门槛过滤、标题带栏目词、raw.keyword 供排期命中
  2. 入库行为：github 条目只落 hot_items，不建灵感选题（不污染 radar 池）
  3. 采集器注册与查询映射（中文栏目词 → 英文查询；无映射跳过）
  4. 合集排期：github 项目（star 倒序）进入 evidence，成为可核验的真实素材
  5. 深挖池同样可命中 github 素材（绑一期"项目深挖"）

运行：.venv/Scripts/python tests/test_github.py
"""
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="p7_check_"))
config.DB_PATH = _TMP / "app.db"
config.BACKUP_DIR = _TMP / "backups"
# 词表用 fixture 的临时副本：建栏目会往词表登记关键词（P5d2），避免污染共享 fixture
_DOMAINS_TMP = _TMP / "domains.yml"
_DOMAINS_TMP.write_text(
    (PROJECT_ROOT / "tests" / "fixtures" / "domains.test.yml").read_text(encoding="utf-8"),
    encoding="utf-8",
)
config.DOMAINS_FILE = _DOMAINS_TMP
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""
config.LLM_MOCK = True
config.GITHUB_MIN_STARS = 100
config.PILLAR_AUTO_SAMPLE = False  # 建栏目不触发真实采样（隔离 RedFox/mcp）

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.collectors import base as collectors_base  # noqa: E402
from app.collectors import github_tools  # noqa: E402
from app.collectors.github_tools import GithubToolsCollector, to_hot_items  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import HotItem, Topic  # noqa: E402
from app.services import pillar as pillar_service  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


_REPOS = [
    {"full_name": "owner/hot-tool", "description": "Convert anything to markdown",
     "stargazers_count": 5000, "forks_count": 300, "open_issues_count": 12,
     "html_url": "https://github.com/owner/hot-tool", "topics": ["ai"], "language": "Python",
     "owner": {"login": "owner"},
     "created_at": "2026-08-01T00:00:00Z", "pushed_at": "2026-08-17T00:00:00Z"},
    {"full_name": "owner/mid-tool", "description": "Batch image editing",
     "stargazers_count": 2000, "forks_count": 120, "open_issues_count": 5,
     "html_url": "https://github.com/owner/mid-tool", "topics": ["ai"], "language": "TypeScript",
     "owner": {"login": "owner"}},
    {"full_name": "owner/another-tool", "description": "Local LLM runner",
     "stargazers_count": 1500, "forks_count": 90, "open_issues_count": 3,
     "html_url": "https://github.com/owner/another-tool", "topics": ["llm"], "language": "Rust",
     "owner": {"login": "owner"}},
    {"full_name": "owner/small-tool", "description": "tiny helper",
     "stargazers_count": 50, "forks_count": 3, "open_issues_count": 0,
     "html_url": "https://github.com/owner/small-tool", "topics": [], "language": "Go",
     "owner": {"login": "owner"}},
]


def main() -> int:
    print(f"临时工作目录：{_TMP}")
    init_db()
    client = TestClient(app)

    print("\n[1] to_hot_items 映射")
    items = to_hot_items(_REPOS, "AI工具")
    check("star 门槛过滤（3 留 / 50 弃）", len(items) == 3, str(len(items)))
    it = items[0]
    check("标题带栏目词（领域可命中）", it.title.startswith("AI工具开源项目｜owner/hot-tool"), it.title[:40])
    check("likes=star、collects=fork、raw.keyword=栏目词",
          it.likes == 5000 and it.collects == 300 and it.raw["keyword"] == "AI工具"
          and it.raw["stars"] == 5000 and it.source == "github")
    check("raw 带 created_at/pushed_at（时效语境）",
          it.raw["created_at"] == "2026-08-01T00:00:00Z"
          and it.raw["pushed_at"] == "2026-08-17T00:00:00Z")

    print("\n[1b] 查询构造：时效优先（新锐项目，不收老牌霸榜）")
    c = GithubToolsCollector()
    from unittest.mock import patch
    captured: dict = {}
    with patch.object(github_tools.httpx, "Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
        MockClient.return_value.__enter__.return_value.get.return_value.raise_for_status = lambda: None
        MockClient.return_value.__enter__.return_value.get.return_value.json.return_value = {"items": _REPOS}
        c._search("AI tools")
        captured = MockClient.return_value.__enter__.return_value.get.call_args.kwargs["params"]
    q = captured["q"]
    check("查询含 created:> 与 stars:>", "created:>" in q and f"stars:>{config.GITHUB_MIN_STARS}" in q, q)
    check("查询含 pushed:> 且按 star 倒序", "pushed:>" in q
          and captured["sort"] == "stars" and captured["order"] == "desc", q)

    print("\n[2] 入库：github 只落 hot_items 不建灵感选题")
    with session_scope() as s:
        result = collectors_base.persist_hot_items(s, items, collector="github_tools")
    check("inserted=3 / topics_created=0",
          result.inserted == 3 and result.topics_created == 0, str(result))
    with session_scope() as s:
        rows = s.scalars(select(HotItem).where(HotItem.source == "github")).all()
    check("hot_items 有 3 行 github", len(rows) == 3)

    print("\n[3] 采集器注册与查询映射")
    check("已注册 github_tools", collectors_base.get_collector("github_tools").name == "github_tools")
    r_p = client.post("/api/pillars", json={
        "name": "本周5个值得装的AI工具", "angle": "每周合集：真实好用的工具",
        "domain": "AI与编程", "slots_per_week": 1, "keywords": ["AI工具"], "active": True})
    check("建合集栏目 201", r_p.status_code == 201, str(r_p.status_code))
    queries = GithubToolsCollector()._queries()
    check("中文栏目词映射为英文查询", queries == [("AI工具", "AI tools")], str(queries))

    print("\n[4] 合集排期吃 github 真实素材")
    plan = client.post("/api/pillars/plan").json()
    d = plan[0]
    check("合集建题", len(d["created"]) == 1, str(d))
    with session_scope() as s:
        t = s.scalars(select(Topic).where(Topic.source == "pillar")).one()
        ev_items = t.evidence["items"]
    check("evidence 首条是 github 项目（真实素材）",
          ev_items and ev_items[0]["url"] == "https://github.com/owner/hot-tool"
          and "owner/hot-tool" in ev_items[0]["title"], str(ev_items)[:120])

    print()
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过")
        for name in FAILURES:
            print(f"  ✗ {name}")
        return 1
    print("PASS：P7 GitHub 工具素材验收全部通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常终止")
        raise SystemExit(1)
