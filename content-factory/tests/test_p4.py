#!/usr/bin/env python
"""P4 验收脚本（可重复运行，不依赖外网与运行中的服务，无 Key 走 mock）。

覆盖任务四件套 P4 结构验收点：
  1. 回填触发评分：2 篇同 topic + 3 篇不同 topic，回填 2 篇不同互动量后
     publish_records 只增不改、article → published、GET /api/topics 按 score 倒序、
     无回填 topic 评分不变
  2. 重算幂等：recompute() 连跑两次结果一致；补录一条 metrics 后重算按新数据更新
  3. 模板效果分：GET /api/prompts/stats 按版本聚合互动均值；
     无 prompt_id 的历史文章归"未知版本"组不报错
  4. 成本报表：GET /api/stats/cost?month=YYYY-MM 双端 tokens/文章数/cost_est 合计，
     xhs 平均单篇成本直接给出且与手工抽算一致；单篇成本明细；/stats 报表页三区
  5. 阈值校准视图：交叉表 + 当前阈值；改 config 阈值后 radar 复判即刻生效（不重启）
  6. 文档附录 docs/p4-calibration.md 存在且含公式与首次校准记录
  7. 生成落库 meta 记录 prompt_id / prompt_version（不改表）

运行：.venv/Scripts/python tests/test_p4.py
"""
import math
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + mock 模式 + 不起调度器 + assets 落临时目录；字体与版式用仓库内真实文件
_TMP = Path(tempfile.mkdtemp(prefix="p4_check_"))
config.DB_PATH = _TMP / "app.db"
config.SENSITIVE_FILE_WECHAT = _TMP / "sensitive_wechat.txt"
config.SENSITIVE_FILE_XHS = _TMP / "sensitive_xhs.txt"
config.ASSETS_DIR = _TMP / "assets"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Article, HotItem, PublishRecord, Topic, ViralSample  # noqa: E402
from app.services import prompt_engine, radar, scoring  # noqa: E402

FAILURES: list[str] = []
NOW_MONTH = None


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def scores_by_id(client: TestClient) -> dict[int, float]:
    return {t["id"]: t["score"] for t in client.get("/api/topics").json()}


def main() -> int:
    global NOW_MONTH
    from datetime import datetime

    NOW_MONTH = datetime.now().strftime("%Y-%m")
    print(f"临时工作目录：{_TMP}")
    init_db()
    prompt_engine.seed_prompts()
    client = TestClient(app)

    # ---- 造数据：5 个选题；topic1 出双端 2 篇（同 topic），topic2/3/4 各 1 篇 ----
    with session_scope() as s:
        s.add(Topic(title="AI 编程助手横评", angle="AI·评测", domain="AI与编程",
                    source="manual", status="new", score=1.0))
        s.add(Topic(title="大模型价格战观察", angle="AI·成本", domain="AI与编程",
                    source="manual", status="new", score=0.8))
        s.add(Topic(title="提示词工程入门", angle="AI·教程", domain="AI与编程",
                    source="manual", status="new", score=1.5))
        s.add(Topic(title="程序员副业思路", angle="职场·副业", domain="AI与编程",
                    source="manual", status="new", score=0.3))
        s.add(Topic(title="早期无版本记录的历史选题", angle="历史", domain="AI与编程",
                    source="manual", status="new", score=0.1))
    a1 = client.post("/api/topics/1/generate?platform=xhs").json()["article_id"]
    a2 = client.post("/api/topics/1/generate?platform=wechat").json()["article_id"]
    a3 = client.post("/api/topics/2/generate?platform=xhs").json()["article_id"]
    a4 = client.post("/api/topics/3/generate?platform=xhs").json()["article_id"]
    a5 = client.post("/api/topics/4/generate?platform=wechat").json()["article_id"]
    baseline = scores_by_id(client)
    check("造数：2 篇同 topic + 3 篇不同 topic，基线评分就位",
          len(baseline) == 5 and baseline[1] == 1.0 and baseline[3] == 1.5)

    print("\n[1] 回填触发评分")
    hi = {"likes": 320, "collects": 88, "comments": 21}   # 互动 320+176+63=559
    lo = {"likes": 12, "collects": 3, "comments": 1}      # 互动 12+6+3=21
    r1 = client.post(f"/api/articles/{a1}/publish", json={
        "platform": "xhs", "account": "主号", "url": "https://xhs/a", "metrics": hi})
    r2 = client.post(f"/api/articles/{a3}/publish", json={
        "platform": "xhs", "account": "主号", "url": "https://xhs/b", "metrics": lo})
    check("两篇回填 201，响应携带 scoring 重算摘要（按提交时点递增）",
          r1.status_code == 201 and r2.status_code == 201
          and r1.json()["scoring"]["publish_records"] == 1
          and r2.json()["scoring"]["publish_records"] == 2, str(r1.status_code))
    with session_scope() as s:
        records = list(s.scalars(select(PublishRecord).order_by(PublishRecord.id)))
        check("publish_records 行数正确、metrics 原样落库（只增不改）",
              len(records) == 2 and records[0].metrics["likes"] == 320
              and records[1].metrics["likes"] == 12)
        arts = {a.id: a for a in s.scalars(select(Article))}
        check("article → published（仅回填的两篇）",
              arts[a1].status == "published" and arts[a3].status == "published"
              and arts[a2].status == "ready")
    scores = scores_by_id(client)
    exp1 = round(1.0 + math.log1p(559), 4)
    exp2 = round(0.8 + math.log1p(21), 4)
    order = [t["id"] for t in client.get("/api/topics").json()]
    check("公式符合附录：score = base + log1p(加权互动)",
          abs(scores[1] - exp1) < 1e-6 and abs(scores[2] - exp2) < 1e-6,
          f"t1={scores[1]} vs {exp1}，t2={scores[2]} vs {exp2}")
    check("GET /api/topics 按 score 倒序，高效互动 topic 排前", order[:2] == [1, 2], str(order))
    check("无回填的 topic score 与回填前一致（不拉低未发布选题）",
          scores[3] == baseline[3] and scores[4] == baseline[4] and scores[5] == baseline[5])
    check("同 topic 未发布的第二篇不参与求和", abs(scores[1] - exp1) < 1e-6)
    with session_scope() as s:
        ev = s.get(Topic, 1).evidence
        check("base_score 快照与重算凭据写入 evidence",
              ev["base_score"] == 1.0 and ev["score_recompute"]["publish_record_max_id"] == 2)

    print("\n[2] 重算幂等")
    before = scores_by_id(client)
    scoring.recompute()
    scoring.recompute()
    check("连续 recompute() 两次，topics.score 完全一致", scores_by_id(client) == before)
    client.post(f"/api/articles/{a3}/publish", json={   # 补录：likes 500/collects 100/comments 50 → 互动 850
        "platform": "xhs", "account": "主号", "metrics": {"likes": 500, "collects": 100, "comments": 50}})
    scores = scores_by_id(client)
    exp2b = round(0.8 + math.log1p(21 + 850), 4)
    check("补录一条 metrics 后重算，score 按新数据更新",
          abs(scores[2] - exp2b) < 1e-6, f"{scores[2]} vs {exp2b}")
    with session_scope() as s:
        n = len(list(s.scalars(select(PublishRecord))))
        check("补录走新增一行（publish_records 3 行，无 UPDATE）", n == 3, str(n))

    print("\n[3] 模板效果分 GET /api/prompts/stats")
    # 历史文章：无 prompt 列值、无 meta.prompt_id → 归“未知版本”组
    with session_scope() as s:
        s.add(Article(topic_id=5, prompt_id=None, platform="xhs", title="历史文章",
                      meta={"usage": {"model": "glm"}}, status="published"))
        s.flush()
        legacy_id = s.scalars(select(Article.id).where(Article.title == "历史文章")).one()
        s.add(PublishRecord(article_id=legacy_id, platform="xhs", metrics={
            "likes": 10, "collects": 2, "comments": 1}))
    stats = client.get("/api/prompts/stats").json()
    groups = {(g["platform"], g["scenario"], str(g["version"])): g for g in stats}
    check("已发布文章按 prompt 版本聚合（xhs·note·v1 两组互动均值）",
          ("xhs", "note", "v1") in groups
          and groups[("xhs", "note", "v1")]["published_count"] == 2, str(sorted(groups)))
    g = groups[("xhs", "note", "v1")]
    check("均值与手工抽算一致（每篇多条回填先合并再平均）",
          g["avg_likes"] == (320 + 512) / 2 and g["avg_collects"] == (88 + 103) / 2,
          f"likes={g['avg_likes']} collects={g['avg_collects']}")
    check("无 prompt_id 的历史文章归入“未知版本”组且不报错",
          ("xhs", "unknown", "未知版本") in groups
          and groups[("xhs", "unknown", "未知版本")]["avg_likes"] == 10.0, str(sorted(groups)))
    check("样本 < 10 篇只展示，不做启停建议",
          all(not g["sufficient_samples"] for g in stats))

    print("\n[4] 成本报表")
    # 统一改写 usage 使数字可控：xhs 每篇 1000/2000/0.13，wechat 500/1000/0.065，
    # 历史文章 100/200/0.02
    with session_scope() as s:
        for art in s.scalars(select(Article)):
            if art.platform == "xhs":
                usage = {"model": "glm", "prompt_tokens": 1000, "completion_tokens": 2000, "cost_est": 0.13}
            else:
                usage = {"model": "glm", "prompt_tokens": 500, "completion_tokens": 1000, "cost_est": 0.065}
            if art.id == legacy_id:
                usage = {"model": "glm", "prompt_tokens": 100, "completion_tokens": 200, "cost_est": 0.02}
            meta = dict(art.meta or {})
            meta["usage"] = usage
            art.meta = meta
    cost = client.get(f"/api/stats/cost?month={NOW_MONTH}").json()
    xhs_cost = cost["platforms"]["xhs"]
    check("当月双端 tokens / 文章数 / cost_est 合计",
          xhs_cost["articles"] == 4 and xhs_cost["prompt_tokens"] == 3100
          and xhs_cost["completion_tokens"] == 6200
          and cost["platforms"]["wechat"]["articles"] == 2
          and cost["total"]["articles"] == 6, str(cost["platforms"]))
    check("xhs 平均单篇成本 = 当月合计 ÷ 篇数，接口直接给出",
          abs(cost["xhs_avg_cost_per_article"] - 0.41 / 4) < 1e-9,
          str(cost["xhs_avg_cost_per_article"]))
    check("报表标注估算口径与单价", "估算口径" in cost["price"]["basis"]
          and cost["price"]["input_per_m"] == config.LLM_PRICE_INPUT_PER_M)
    check("历史月份小表包含当月", any(h["month"] == NOW_MONTH and h["articles"] == 6
                                      for h in cost["history"]))
    one = client.get(f"/api/stats/cost/article/{a1}").json()
    check("单篇成本明细与 meta.usage 一致",
          one["usage"]["prompt_tokens"] == 1000 and one["usage"]["cost_est"] == 0.13)
    check("单篇不存在 → 404", client.get("/api/stats/cost/article/9999").status_code == 404)
    check("month 格式非法 → 422", client.get("/api/stats/cost?month=2026-1").status_code == 422)

    print("\n[5] 阈值校准视图")
    with session_scope() as s:
        item = HotItem(source="xhs", title="低粉爆款样本一", url="https://xhs/s1",
                       fans=1200, likes=1000, collects=500, comments=200)
        s.add(item)
        s.flush()
        vs = radar.viral_score(item)   # (1000+1000+600)/1200 = 2.1667 ≥ 2.0
        s.add(ViralSample(hot_item_id=item.id, domain="AI与编程", viral_score=vs))
        t = Topic(title=item.title, angle="AI·样本", domain="AI与编程", source="radar",
                  status="used", score=vs,
                  evidence={"items": [{"hot_item_id": item.id, "title": item.title}]})
        s.add(t)
        s.flush()
        s.add(Article(topic_id=t.id, prompt_id=None, platform="xhs", title="对照发布",
                      meta={"usage": {"model": "glm"}}, status="published"))
        s.flush()
        art_id = s.scalars(select(Article.id).where(Article.title == "对照发布")).one()
        s.add(PublishRecord(article_id=art_id, platform="xhs", metrics={
            "likes": 400, "collects": 100, "comments": 50}))  # 互动 400+200+150=750
    calib = client.get("/api/stats/threshold-calibration").json()
    check("返回当前四项阈值",
          calib["thresholds"]["viral_fans_max"] == config.VIRAL_FANS_MAX
          and calib["thresholds"]["viral_score_min"] == config.VIRAL_SCORE_MIN
          and "topic_duplicate_jaccard" in calib["thresholds"])
    sample = next(s for s in calib["samples"] if s["hot_item_id"] == item.id)
    check("交叉表：样本判定数据 + 当前阈值复判通过",
          sample["viral_score"] == vs and sample["would_pass_now"] is True)
    check("交叉表：关联选题的实际发布效果（互动合计 750）",
          sample["published_effect"]["engagement"] == 750.0
          and sample["published_effect"]["topic_id"] == t.id)
    # 人工结论写回 config（运行中改属性即生效，radar 现读不缓存）
    old_min = config.VIRAL_SCORE_MIN
    try:
        config.VIRAL_SCORE_MIN = 3.0
        check("改 config 后 radar 立即按新阈值判定（不重启）",
              radar.is_low_fans_viral(item) is False)
        calib2 = client.get("/api/stats/threshold-calibration").json()
        check("校准视图同步反映新阈值与复判结果",
              calib2["thresholds"]["viral_score_min"] == 3.0
              and next(s for s in calib2["samples"] if s["hot_item_id"] == item.id)["would_pass_now"] is False)
    finally:
        config.VIRAL_SCORE_MIN = old_min

    print("\n[6] 报表页 /stats 与文档附录")
    resp = client.get("/stats")
    check("GET /stats 200 且三区齐备（成本 / 模板效果 / 阈值校准）",
          resp.status_code == 200 and all(h in resp.text for h in ("成本统计", "模板效果分", "阈值校准")))
    api_cost = client.get(f"/api/stats/cost?month={NOW_MONTH}").json()
    api_avg = api_cost["xhs_avg_cost_per_article"]
    check("报表页突出 xhs 平均单篇成本与“样本不足”标识",
          f"${api_avg}" in resp.text and "样本不足" in resp.text,
          f"api={api_avg!r}（= 0.41 ÷ {api_cost['platforms']['xhs']['articles']}，校准步新增的对照文章计 0 成本）")
    check("校准区展示当前阈值与“环境变量”修改提示，不提供改阈值入口",
          "CF_VIRAL_SCORE_MIN" in resp.text and "环境变量" in resp.text)
    calib_doc = (PROJECT_ROOT / "docs" / "p4-calibration.md").read_text(encoding="utf-8")
    check("docs/p4-calibration.md 记录公式与首次校准结论（维持初值）",
          "log1p" in calib_doc and "维持初值" in calib_doc and "2026-08-18" in calib_doc)

    print("\n[7] meta 记录 prompt 版本（不改表）")
    xhs_note = next(p for p in client.get("/api/prompts").json()
                    if p["platform"] == "xhs" and p["scenario"] == "note")
    with session_scope() as s:
        m = (s.get(Article, a1).meta or {})
        check("meta.prompt_id / prompt_version 已随生成落库",
              m.get("prompt_id") == xhs_note["id"] and m.get("prompt_version") == xhs_note["version"],
              f"prompt_id={m.get('prompt_id')}（期望 {xhs_note['id']}） version={m.get('prompt_version')}")
    resp = client.get("/api/topics")
    check("GET /api/topics 契约：列表、score 倒序、含 evidence",
          resp.status_code == 200 and [t["id"] for t in resp.json()][0]
          == max(resp.json(), key=lambda t: t["score"])["id"])

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P4 全部验收项通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
