#!/usr/bin/env python
"""P-1a 验收脚本（可重复运行，不依赖外网与运行中的服务）。

覆盖：
  1. 第 5 章 8 张表全量建表
  2. 手动触发采集：入库、URL 去重、领域过滤
  3. 二次采集零新增（去重生效）
  4. source=radar 候选选题生成 + 撞题合并（evidence 追加、score 取大）
  5. 备份演练（保留最近 7 份）
  6. 告警演练（本地 HTTP 接收端验证通道与报文格式）
  7. 选题过期归档、hot_items 90 天清理

运行：.venv/bin/python tests/test_p1a.py
"""
import json
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 配置指向临时库与本地 fixture（file:// 数据源走与线上一致的解析/入库代码）
_TMP = Path(tempfile.mkdtemp(prefix="p1a_check_"))
config.DB_PATH = _TMP / "app.db"
config.BACKUP_DIR = _TMP / "backups"
config.DOMAINS_FILE = PROJECT_ROOT / "tests" / "fixtures" / "domains.test.yml"
config.RSSHUB_BASE_URL = "file://" + str(PROJECT_ROOT / "tests" / "fixtures")
config.NOTIFY_WEBHOOK = ""

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.collectors import base as collectors_base  # noqa: E402
from app.collectors import hotboard  # noqa: E402,F401  注册 hotboard 采集器
from app.db import get_engine, init_db, session_scope  # noqa: E402
from app.models import ALL_MODELS, HotItem, Topic  # noqa: E402
from app.services import notify, radar, scheduler as scheduler_service  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


class AlertReceiver(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        AlertReceiver.received.append(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):  # 静默访问日志
        pass


def main() -> int:
    print(f"临时工作目录：{_TMP}")

    print("\n[1] 建库建表（第 5 章全量 8 张表）")
    init_db()
    with get_engine().connect() as conn:
        from sqlalchemy import text

        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    expected = {model.__tablename__ for model in ALL_MODELS}
    check("8 张表齐全", expected <= tables, "、".join(sorted(expected)))

    print("\n[2] 手动触发采集（第 1 次）")
    r1 = collectors_base.run_collector("hotboard")
    print(f"    结果：{r1.model_dump()}")
    check("拉取 17 条 fixture", r1.fetched == 17, f"fetched={r1.fetched}")
    check("跨源同 URL 去重生效", r1.duplicates_skipped == 1, f"duplicates_skipped={r1.duplicates_skipped}")
    check("领域过滤生效（未命中不入库）", r1.filtered_out == 7, f"filtered_out={r1.filtered_out}")
    check("命中入库 9 条", r1.inserted == 9, f"inserted={r1.inserted}")
    check("撞题合并生效（近同标题二合一）", r1.topics_merged == 1 and r1.topics_created == 8,
          f"created={r1.topics_created}, merged={r1.topics_merged}")

    print("\n[3] 手动触发采集（第 2 次，应零新增）")
    r2 = collectors_base.run_collector("hotboard")
    print(f"    结果：{r2.model_dump()}")
    check("二次采集零新增", r2.inserted == 0, f"inserted={r2.inserted}")
    check("全部命中条目被识别为重复", r2.duplicates_skipped == r1.inserted + 1,
          f"duplicates_skipped={r2.duplicates_skipped}")
    check("不重复建选题", r2.topics_created == 0 and r2.topics_merged == 0)

    print("\n[4] source=radar 候选选题")
    with session_scope() as session:
        topics = session.query(Topic).filter(Topic.source == "radar").all()
        check("至少 1 条 radar 选题", len(topics) >= 1, f"共 {len(topics)} 条")
        ok_status = all(t.status == "new" for t in topics)
        check("自动选题一律 status=new", ok_status)
        ok_ttl = all(t.expires_at and abs(
            (t.expires_at - t.created_at).total_seconds() - config.TOPIC_TTL_HOURS * 3600
        ) < 60 for t in topics)
        check("expires_at = created_at + 72h", ok_ttl)
        ok_domain = all(t.domain and t.evidence and t.evidence.get("items") for t in topics)
        check("domain 与 evidence 快照齐备", ok_domain)
        merged = [
            t for t in topics if t.evidence and len(t.evidence.get("items", [])) == 2
        ]
        check("合并选题的 evidence 含 2 条样本", len(merged) == 1, f"{len(merged)} 条")

        dup = HotItem(source="weibo", title="重复 URL 直插测试", url=r"https://example.com/hot/llm-price-war")
        session.add(dup)
        raised = False
        try:
            session.flush()
        except IntegrityError:
            raised = True
        session.rollback()  # 无论如何回滚，避免污染后续备份断言
        check("url 唯一约束拦截重复行", raised)

    print("\n[5] 备份演练（保留最近 7 份）")
    for i in range(1, 10):  # 预置 9 份陈旧备份
        (config.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        (config.BACKUP_DIR / f"app_2026010{i}.db").write_bytes(b"stale")
    dest = scheduler_service.backup_database()
    files = sorted(p.name for p in config.BACKUP_DIR.glob("app_*.db"))
    check("生成当日备份", dest.name == f"app_{datetime.now():%Y%m%d}.db", dest.name)
    check("只保留最近 7 份", len(files) == 7, "、".join(files))
    import sqlite3

    with sqlite3.connect(dest) as conn:
        count = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
    check("备份文件可读且含热榜数据", count == r1.inserted, f"hot_items={count}")

    print("\n[6] 告警演练（本地接收端）")
    AlertReceiver.received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), AlertReceiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config.NOTIFY_WEBHOOK = f"http://127.0.0.1:{server.server_address[1]}/hook"
    ok = notify.send_alert("WARN", "test", "通道演练", "P-1a 验收")
    check("告警外发成功", ok is True)
    check("报文格式 [级别] 模块 - 事件",
          len(AlertReceiver.received) == 1
          and AlertReceiver.received[0].get("title") == "[WARN] test - 通道演练",
          str(AlertReceiver.received[:1]))
    server.shutdown()
    config.NOTIFY_WEBHOOK = ""

    print("\n[7] 选题过期归档与 90 天清理")
    with session_scope() as session:
        stale_topic = Topic(
            title="已过期的雷达选题", domain="AI与编程", source="radar", status="new",
            expires_at=datetime.now() - timedelta(hours=1),
        )
        session.add(stale_topic)
        stale_item = HotItem(
            source="weibo", title="91 天前的旧热榜",
            url="https://example.com/hot/very-old", captured_at=datetime.now() - timedelta(days=91),
        )
        session.add(stale_item)
        session.flush()
        archived = radar.archive_expired_topics(session)
        removed = radar.cleanup_hot_items(session)
        check("到期 new 选题归档为 archived", archived == 1, f"archived={archived}")
        check("90 天前 hot_items 物理删除", removed == 1, f"removed={removed}")

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P-1a 全部验收项通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
