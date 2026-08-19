#!/usr/bin/env python
"""P-1b 验收脚本（可重复运行，不依赖外网、mcp 服务与真实小红书账号）。

覆盖任务四件套 P-1b 结构验收点：
  1. fans 探针记录存在且结论显式（docs/p-1b-fans-probe.md）
  2. M2 协议：mock/录制响应下 fetch → HotItem 列表、URL 去重、领域过滤、
     解析容错（"1.2万"/"1,200"）、fans 缺失降级（只落笔记级数据）
  3. M3 打分：viral_score 公式三组样本 + fans=0 不除零；阈值判定边界
  4. 落库与建题：入选样本写 viral_samples 并自动生成 topics(source=radar,
     status=new)，evidence 含 URL/作者/互动数/viral_score/命中关键词
  5. 撞题去重：Jaccard ≥ 0.5 合并（不新建、evidence 追加、score 取大）；< 0.5 新建
  6. 人工喂样本：与自动样本同一管线；缺 fans / 非法数字 / 非法 URL → 422；
     手填新领域 → 201（领域自由填写，不卡词表）
     重复 URL → 409，不写半成品
  7. A3 模板幂等入库 + 周度拆解（mock LLM）：reason 回写、tag_library.heat 累计
  8. 熔断：连续 3 次失败 → 熔断 + 告警一次 + 拒绝执行；人工恢复后才能继续
  9. API/页面契约：/api/viral-samples 排序、/api/admin/collectors 状态、/viral 页面

运行：.venv/Scripts/python tests/test_p1b.py
"""
import json
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + fixture 领域词表 + mock LLM + 不起调度器；mcp 指向本机回环（探针不可用）
_TMP = Path(tempfile.mkdtemp(prefix="p1b_check_"))
config.DB_PATH = _TMP / "app.db"
config.BACKUP_DIR = _TMP / "backups"
config.DOMAINS_FILE = PROJECT_ROOT / "tests" / "fixtures" / "domains.test.yml"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""
config.XHS_SAMPLE_KEYWORDS = ["AI"]  # 单一检索词，mock 响应一轮返回全部笔记
config.REDFOX_API_KEY = ""  # 隔离真实付费数据源：强制走 mcp 分支（测试桩在 _search）

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.collectors import base as collectors_base  # noqa: E402
from app.collectors import xhs_sample  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CollectorState, HotItem, TagLibrary, Topic, ViralSample  # noqa: E402
from app.schemas import HotItem as HotItemSchema  # noqa: E402
from app.services import prompt_engine, radar  # noqa: E402

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


# mock/录制响应：一次 search_notes 返回 5 条笔记（含重复 URL / 非领域 / 无 fans / 低赞）
_MOCK_NOTES = [
    {  # 入选低粉爆款：fans=1200, likes=1200, collects=450, comments=300 → 2.5
        "note_id": "viral1", "title": "程序员用AI提效的实战心得",
        "user": {"nickname": "小A", "fans": "1,200"},
        "liked_count": "1200", "collected_count": 450, "comment_count": 300,
    },
    {  # 与上一条同 URL（note_id 相同）→ 去重
        "note_id": "viral1", "title": "程序员用AI提效的实战心得（重复）",
        "user": {"nickname": "小A"}, "liked_count": "10",
    },
    {  # 标题不命中任何领域关键词 → 过滤
        "note_id": "offdomain", "title": "周末露营装备清单与路线",
        "user": {"nickname": "小C"}, "liked_count": "5000",
    },
    {  # 无 fans 字段 → 降级：只落 hot_items，不参与低粉爆款判定
        "note_id": "nofans", "title": "自媒体博主复盘爆款文案方法论",
        "user": {"nickname": "小D"}, "liked_count": "800", "collected_count": 90, "comment_count": 40,
    },
    {  # fans 可用但点赞未过 VIRAL_LIKES_MIN 预筛 → 落库不判定
        "note_id": "lowlikes", "title": "AI编程工具横向测评",
        "user": {"nickname": "小E", "fans": "1000"},
        "liked_count": "100", "collected_count": 20, "comment_count": 10,
    },
]


def main() -> int:
    print(f"临时工作目录：{_TMP}")

    print("\n[1] fans 探针记录（docs/p-1b-fans-probe.md）")
    probe_doc = PROJECT_ROOT / "docs" / "p-1b-fans-probe.md"
    check("探针记录文件存在", probe_doc.exists())
    text = probe_doc.read_text(encoding="utf-8") if probe_doc.exists() else ""
    check("结论显式（fans_available / 降级模式依据）", "fans_available" in text and "降级模式" in text)

    print("\n[2] 建库与 A3 种子模板幂等入库")
    init_db()
    from _support import seed_domains_from  # noqa: E402  词表入库后的种子导入（幂等）

    seed_domains_from(config.DOMAINS_FILE)
    seeded = prompt_engine.seed_prompts()
    check("A3 模板入库 xhs+teardown+v1", "xhs+teardown+v1" in seeded, "、".join(seeded))
    again = prompt_engine.seed_prompts()
    check("二次入库幂等（不重复不覆盖）", "xhs+teardown+v1" not in again, "、".join(again))

    print("\n[3] M3 打分数值（公式与阈值边界）")
    base_score = radar.viral_score(HotItemSchema(source="xhs", title="t", fans=5000, likes=500))
    check("fans=5000,likes=500 → 0.1", base_score == 0.1, f"{base_score}")
    check("该样本不入选", not radar.is_low_fans_viral(
        HotItemSchema(source="xhs", title="t", fans=5000, likes=500)))
    hit = radar.viral_score(HotItemSchema(source="xhs", title="t", fans=100, likes=100, collects=50, comments=20))
    check("fans=100,likes=100,collects=50,comments=20 → 2.6", hit == 2.6, f"{hit}")
    check("该样本入选", radar.is_low_fans_viral(
        HotItemSchema(source="xhs", title="t", fans=100, likes=100, collects=50, comments=20)))
    zero_fans = radar.viral_score(HotItemSchema(source="xhs", title="t", fans=0, likes=50))
    check("fans=0 按 max(fans,1) 计算不除零", zero_fans == 50.0, f"{zero_fans}")
    big_fans = HotItemSchema(source="xhs", title="t", fans=20000, likes=99999)
    check("fans 超上限直接否决", not radar.is_low_fans_viral(big_fans))
    check("互动数容错：'1.2万'→12000", xhs_sample._to_int("1.2万") == 12000)
    check("互动数容错：'1,200'→1200", xhs_sample._to_int("1,200") == 1200)
    check("互动数容错：None→0", xhs_sample._to_int(None) == 0)
    parsed = xhs_sample.parse_search_notes(_MOCK_NOTES)
    check("M2 协议：解析结果为 HotItem 列表",
          len(parsed) == 5 and all(isinstance(p, HotItemSchema) for p in parsed))
    check("M2 协议：fans 抽取（user.fans='1,200'→1200）", parsed[0].fans == 1200, f"{parsed[0].fans}")
    check("M2 协议：无 fans 字段 → 0（不伪造）", parsed[3].fans == 0)

    print("\n[4] M2 采样落库（mock 响应，第 1 次）")
    collector_cls = xhs_sample.XhsSampleCollector
    original_search = collector_cls._search
    collector_cls._search = lambda self, keyword: list(_MOCK_NOTES)
    r1 = collectors_base.run_collector("xhs_sample")
    print(f"    结果：{r1.model_dump()}")
    check("fetched=5", r1.fetched == 5)
    check("同 URL 重复只入库一次（duplicates_skipped=1）", r1.duplicates_skipped == 1)
    check("非领域条目被过滤（filtered_out=1）", r1.filtered_out == 1)
    check("入库 3 条", r1.inserted == 3)
    check("入选低粉爆款 1 条（viral_created=1）", r1.viral_created == 1)
    check("自动建题 1 条", r1.topics_created == 1 and r1.topics_merged == 0)

    print("\n[5] 二次采样零新增（去重）")
    r2 = collectors_base.run_collector("xhs_sample")
    check("零新增", r2.inserted == 0 and r2.viral_created == 0, f"inserted={r2.inserted}")

    print("\n[6] 落库与建题证据（viral_samples + topics.evidence）")
    with session_scope() as session:
        samples = session.scalars(select(ViralSample)).all()
        check("viral_samples 落库 1 条", len(samples) == 1)
        sample = samples[0]
        check("实时判定 title_pattern=auto / reason=rule",
              sample.title_pattern == "auto" and sample.reason == "rule")
        check("viral_score=2.5", sample.viral_score == 2.5, f"{sample.viral_score}")
        hot = session.get(HotItem, sample.hot_item_id)
        topic_rows = session.scalars(select(Topic).where(Topic.source == "radar")).all()
        xhs_topics = [t for t in topic_rows if t.evidence and t.evidence["items"][0].get("source") == "xhs"]
        check("自动生成 topics(source=radar, status=new)", len(xhs_topics) == 1
              and xhs_topics[0].status == "new")
        ev = xhs_topics[0].evidence["items"][0]
        check("evidence 含 URL/作者/互动数/viral_score/命中关键词",
              ev["url"].endswith("/explore/viral1") and ev["author"] == "小A"
              and ev["metrics"] == {"fans": 1200, "likes": 1200, "collects": 450, "comments": 300}
              and ev["viral_score"] == 2.5 and ev["matched_keyword"], str(ev))
        check("topics.score 取 viral_score", xhs_topics[0].score == 2.5)

    client = TestClient(app)  # 不进 lifespan（不起调度器）；init_db 与种子已手动完成

    print("\n[7] 人工喂样本 API（与自动样本同一管线）")
    payload_a = {
        "url": "https://www.xiaohongshu.com/explore/manual-a",
        "title": "程序员用AI提效的三个隐藏技巧", "author": "手工甲",
        "fans": 1000, "likes": 800, "collects": 300, "comments": 200, "domain": "AI与编程",
    }
    resp = client.post("/api/viral-samples/manual", json=payload_a)
    check("合法样本 201", resp.status_code == 201, str(resp.status_code))
    body_a = resp.json()
    check("入选并建题（viral_sample_id / topic_id 齐全）",
          body_a["viral"] and body_a["viral_sample_id"] and body_a["topic_id"], str(body_a))
    check("打分与自动样本同一公式（2.0）", body_a["viral_score"] == 2.0, str(body_a["viral_score"]))

    print("\n[8] 撞题去重（Jaccard ≥ 0.5 合并 / < 0.5 新建）")
    with session_scope() as session:
        before = len(session.scalars(select(Topic.id).where(Topic.source == "radar")).all())
    payload_b = {
        "url": "https://www.xiaohongshu.com/explore/manual-b",
        "title": "程序员用AI提效的三个隐藏玩法", "author": "手工乙",
        "fans": 100, "likes": 500, "collects": 50, "comments": 20, "domain": "AI与编程",
    }
    resp = client.post("/api/viral-samples/manual", json=payload_b)
    body_b = resp.json()
    with session_scope() as session:
        after = len(session.scalars(select(Topic.id).where(Topic.source == "radar")).all())
        merged_topic = session.get(Topic, body_a["topic_id"])
    check("相似标题不新建 topic（Jaccard≥0.5）", after == before and body_b["topic_id"] == body_a["topic_id"],
          f"before={before} after={after}")
    check("evidence 追加样本链接", len(merged_topic.evidence["items"]) == 2
          and merged_topic.evidence["items"][1]["url"] == payload_b["url"])
    check("score 取较大值（2.0 → 6.6）", merged_topic.score == 6.6, f"{merged_topic.score}")

    payload_c = {
        "url": "https://www.xiaohongshu.com/explore/manual-c",
        "title": "打工人存钱攻略每月强制储蓄", "author": "手工丙",
        "fans": 200, "likes": 600, "collects": 100, "comments": 50, "domain": "效率与副业",
    }
    resp = client.post("/api/viral-samples/manual", json=payload_c)
    with session_scope() as session:
        final = len(session.scalars(select(Topic.id).where(Topic.source == "radar")).all())
    check("不相似标题（Jaccard<0.5）新建 topic", resp.status_code == 201 and final == before + 1,
          f"final={final}")

    print("\n[9] 人工喂样本入参校验（不写半成品）")
    viral_before = None
    with session_scope() as session:
        viral_before = len(session.scalars(select(ViralSample.id)).all())
    cases = [
        ("缺 fans → 422", {**payload_c, "url": "https://x/com/no-fans"}, {"fans": None}, 422),
        ("fans 为负 → 422", {**payload_c, "url": "https://x/com/bad-fans"}, {"fans": -5}, 422),
        ("likes 非数字 → 422", {**payload_c, "url": "https://x/com/bad-likes"}, {"likes": "abc"}, 422),
        ("URL 非法 → 422", {**payload_c}, {"url": "ftp://not-http"}, 422),
        ("重复 URL → 409", {**payload_a}, {}, 409),
    ]
    for name, base, patch, expected in cases:
        body = {k: v for k, v in {**base, **patch}.items() if v is not None or k == "fans"}
        if patch.get("fans") is None and "fans" in patch:
            body.pop("fans", None)
        resp = client.post("/api/viral-samples/manual", json=body)
        ok = resp.status_code == expected
        if not ok:
            print(f"      got {resp.status_code}: {resp.text[:200]}")
        check(name, ok, f"{resp.status_code}")
    with session_scope() as session:
        viral_after = len(session.scalars(select(ViralSample.id)).all())
        stale = session.scalars(select(HotItem.id).where(HotItem.url.like("https://x/com/%"))).all()
    check("非法/重复请求未写入任何数据", viral_after == viral_before and not stale,
          f"viral {viral_before}→{viral_after}, stale={stale}")
    # 领域自由填写：手填新领域不再 422，正常走管线入库（放在不写半成品断言之后）
    resp_free = client.post("/api/viral-samples/manual", json={
        **payload_c, "url": "https://x/free-domain", "domain": "不存在的领域"})
    check("手填新领域 → 201（领域自由填写）", resp_free.status_code == 201,
          f"{resp_free.status_code}: {resp_free.text[:120]}")

    print("\n[10] 样本列表 API 与页面")
    resp = client.get("/api/viral-samples?domain=AI与编程")
    rows = resp.json()
    scores = [r["viral_score"] for r in rows]
    check("GET /api/viral-samples 按分倒序", resp.status_code == 200
          and scores == sorted(scores, reverse=True), str(scores))
    check("含 hot_item 互动数据摘要", all("hot_item" in r and "fans" in r["hot_item"] for r in rows))
    resp = client.get("/api/admin/collectors")
    states = {s["name"]: s for s in resp.json()["collectors"]}
    check("采集器状态接口可见 xhs_sample", "xhs_sample" in states
          and states["xhs_sample"]["status"] != "open")
    resp = client.get("/viral")
    check("管理页 /viral 200 且含人工喂样本表单",
          resp.status_code == 200 and "feedSample" in resp.text and "viral-samples/manual" in resp.text)

    print("\n[11] 周度拆解（mock LLM）：reason 回写 + 标签热度累计")
    summary = radar.run_weekly_teardown()
    print(f"    结果：{summary}")
    check("拆解覆盖当周全部样本", summary["samples"] >= 4 and summary["reasons_updated"] == summary["samples"])
    with session_scope() as session:
        reasons = session.scalars(select(ViralSample.reason)).all()
        tags = session.scalars(select(TagLibrary).where(TagLibrary.tag == "AI工具实测")).all()
    check("样本 reason 被更新（不再等于 rule）", all(r and r != "rule" for r in reasons))
    check("tag_library.heat 累计 1", len(tags) == 1 and tags[0].heat == 1, str([(t.tag, t.heat) for t in tags]))
    resp = client.post("/api/collectors/xhs_teardown/run")
    check("手动触发拆解接口 200", resp.status_code == 200 and resp.json()["reasons_updated"] >= 1,
          resp.text[:120])
    with session_scope() as session:
        heat = session.scalars(select(TagLibrary).where(TagLibrary.tag == "AI工具实测")).one().heat
    check("再拆解一次 heat 累计到 2", heat == 2, f"heat={heat}")

    print("\n[12] 熔断演练（无效 mcp 地址，连续 3 次失败）")
    AlertReceiver.received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), AlertReceiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config.NOTIFY_WEBHOOK = f"http://127.0.0.1:{server.server_address[1]}/hook"
    collector_cls._search = original_search
    config.XHS_MCP_BASE_URL = "http://127.0.0.1:9"  # 无效地址模拟 mcp 不可达
    failed = 0
    for _ in range(3):
        try:
            collectors_base.run_collector("xhs_sample")
        except collectors_base.CircuitOpenError:
            check("第 3 次之前不应熔断拒绝", False)
        except Exception:
            failed += 1
    check("连续 3 次失败", failed == 3, f"failed={failed}")
    import sqlite3

    with sqlite3.connect(config.DB_PATH) as conn:
        row = conn.execute(
            "SELECT name,status,consecutive_failures FROM collector_state WHERE name='xhs_sample'"
        ).fetchone()
    check("collector_state 状态为熔断（open）且计数 3", row == ("xhs_sample", "open", 3), str(row))
    breaker_alerts = [a for a in AlertReceiver.received if "熔断" in a.get("title", "")]
    check("熔断告警恰好外发一次", len(breaker_alerts) == 1,
          str([a.get("title") for a in AlertReceiver.received]))
    try:
        collectors_base.run_collector("xhs_sample")
        check("熔断后直接调用被拒绝", False)
    except collectors_base.CircuitOpenError:
        check("熔断后直接调用被拒绝", True)
    resp = client.post("/api/collectors/xhs_sample/run")
    check("熔断后 API 触发返回 409", resp.status_code == 409, str(resp.status_code))
    resp = client.post("/api/admin/collectors/xhs_sample/resume")
    check("人工恢复 200 且计数清零", resp.status_code == 200)
    resp = client.post("/api/admin/collectors/xhs_sample/resume")
    check("未熔断时恢复返回 409", resp.status_code == 409)
    config.XHS_MCP_BASE_URL = "http://localhost:18060"
    collector_cls._search = lambda self, keyword: list(_MOCK_NOTES)
    r3 = collectors_base.run_collector("xhs_sample")
    with session_scope() as session:
        state = session.scalars(select(CollectorState).where(CollectorState.name == "xhs_sample")).one()
    check("恢复后可再次执行且计数清零", r3.inserted == 0 and state.status == "enabled"
          and state.consecutive_failures == 0, f"inserted={r3.inserted}")
    server.shutdown()
    config.NOTIFY_WEBHOOK = ""
    collector_cls._search = original_search

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P-1b 全部结构验收项通过")
    print(f"（真实质量验收见 docs/p-1b-fans-probe.md 与 README：连续 3 天采样 / 人工录入，viral_samples ≥ 5）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
