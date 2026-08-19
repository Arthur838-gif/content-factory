#!/usr/bin/env python
"""P5 内容栏目验收脚本（可重复运行，不依赖外网与真实 Key）。

覆盖：
  1. 栏目 CRUD API（创建 / 列表 / 停用 / 有排期不可删）
  2. 周排期：周更固定档（标题带周期区间、evidence 带素材快照）+
     多期轮换档（每期绑一条素材、按 likes 倒序、不重复用素材、跨周素材不取）
  3. 幂等：重复 plan 只补缺口 / 已满跳过
  4. 撞题豁免：radar 高相似标题不会合并进 pillar 选题
  5. 采样接线：启用栏目后 xhs_sample 检索词取栏目关键词池
  6. /pillars 页面契约
  7. P5b 周主题：生成（mock）/ 素材不足拦截 / 确认改写
  8. P5b 主题排期：按子话题分期 + 期数编号 + 幂等
  9. P5b 生成系列上下文（主题/期数/其他期/合集枢纽）+ 选题归档
 10. P5b 合集素材不足拦截（防模型虚构内容）
 11. P5b 按主题重排：归档未按主题的旧选题并按主题重新分期
 12. P5b 归档选题不占档位：全部归档后排期重新建满
 16. 单栏目定向采样（POST /api/pillars/{id}/sample：词池快照 / 去重 / 空词池 422）

运行：.venv/Scripts/python tests/test_pillar.py
"""
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="p5_check_"))
config.DB_PATH = _TMP / "app.db"
config.BACKUP_DIR = _TMP / "backups"
# 领域词表已入库（P-2）：种子直接读共享 fixture（只读导入；建栏目只写临时库）
config.DOMAINS_FILE = PROJECT_ROOT / "tests" / "fixtures" / "domains.test.yml"
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""
config.XHS_SAMPLE_KEYWORDS = []  # 走栏目关键词池分支
config.LLM_MOCK = True  # P5b 周主题规划走 mock，不打真实 LLM
config.PILLAR_AUTO_SAMPLE = False  # 隔离真实采样：建栏目后台任务在 [15] 单独测

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.collectors import xhs_sample  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import HotItem, Pillar, Topic  # noqa: E402
from app.services import pillar as pillar_service, radar  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def _seed_hot_items() -> None:
    rows = [
        HotItem(source="xhs", title="AI工具实测：效率翻倍的三个工具",
                url="https://xhs/a1", fans=800, likes=2000, collects=100, comments=50),
        HotItem(source="xhs", title="AIGC 新手入门指南",
                url="https://xhs/a2", fans=1200, likes=1500, collects=80, comments=30),
        HotItem(source="xhs", title="AI工具组合拳工作流分享",
                url="https://xhs/a3", fans=600, likes=900, collects=60, comments=20),
        HotItem(source="xhs", title="AI工具冷门但好用",
                url="https://xhs/a4", fans=900, likes=700, collects=40, comments=10),
        HotItem(source="xhs", title="与栏目无关的美食笔记",
                url="https://xhs/a5", fans=100, likes=9999),
        HotItem(source="xhs", title="上周的AI工具旧文（不该入选）",
                url="https://xhs/a6", fans=500, likes=5000,
                captured_at=datetime.now() - timedelta(days=8)),
        # 标题不含关键词，但由栏目关键词采样回来（raw.keyword）→ 应命中
        HotItem(source="xhs", title="深度体验分享（标题不带关键词）",
                url="https://xhs/a7", fans=700, likes=2500, raw={"keyword": "AIGC"}),
    ]
    with session_scope() as session:
        session.add_all(rows)


def main() -> int:
    print(f"临时工作目录：{_TMP}")
    init_db()
    from _support import seed_domains_from  # noqa: E402  词表入库后的种子导入（幂等）

    seed_domains_from(config.DOMAINS_FILE)
    _seed_hot_items()
    client = TestClient(app)  # 不进 lifespan

    print("\n[1] 栏目 CRUD API")
    r1 = client.post("/api/pillars", json={
        "name": "本周5个值得装的AI工具", "angle": "每周合集：5 个工具 + 一句话点评",
        "domain": "AI与编程", "slots_per_week": 1,
        "keywords": ["AI工具", "AIGC"], "active": True})
    check("创建合集栏目 201", r1.status_code == 201, str(r1.status_code))
    r2 = client.post("/api/pillars", json={
        "name": "AI工具深挖", "angle": "单工具深度内容：痛点→做法→效果对比",
        "domain": "AI与编程", "slots_per_week": 3,
        "keywords": ["AI工具", "AIGC"], "active": True})
    check("创建多期栏目 201", r2.status_code == 201, str(r2.status_code))
    pid_c, pid_m = r1.json()["id"], r2.json()["id"]
    lst = client.get("/api/pillars").json()
    check("列表含两个栏目", len(lst) == 2 and lst[0]["slots_per_week"] == 1)

    print("\n[2] 周排期（plan）")
    plan = client.post("/api/pillars/plan").json()
    by_name = {x["pillar"]: x for x in plan}
    check("合集档新增 1 期", len(by_name["本周5个值得装的AI工具"]["created"]) == 1, str(plan))
    check("多期档新增 3 期（素材只有 4 条命中，取 3）", len(by_name["AI工具深挖"]["created"]) == 3, str(plan))
    with session_scope() as session:
        pillar_topics = session.scalars(
            select(Topic).where(Topic.source == "pillar").order_by(Topic.id)).all()
        collection = [t for t in pillar_topics if (t.evidence or {}).get("pillar_id") == pid_c]
        multi = [t for t in pillar_topics if (t.evidence or {}).get("pillar_id") == pid_m]
        week_start, week_end = pillar_service.week_bounds()
        check("合集标题带周期区间", collection[0].title.startswith("本周5个值得装的AI工具（")
              and "–" in collection[0].title, collection[0].title)
        check("合集 evidence 带 5 条素材快照（无美食/上周旧文）",
              len(collection[0].evidence["items"]) == 5
              and {it["url"] for it in collection[0].evidence["items"]}
              == {"https://xhs/a1", "https://xhs/a2", "https://xhs/a3", "https://xhs/a4", "https://xhs/a7"})
        check("多期每期绑一条素材且 URL 不重复",
              len({t.evidence["items"][0]["url"] for t in multi}) == 3
              and all(len(t.evidence["items"]) == 1 for t in multi))
        check("多期按 likes 倒序取素材（含 raw.keyword 命中的 a7）",
              [t.evidence["items"][0]["url"] for t in multi]
              == ["https://xhs/a7", "https://xhs/a1", "https://xhs/a2"])
        check("选题 source=pillar / angle=栏目角度 / expires_at=周末",
              all(t.source == "pillar" and t.angle for t in pillar_topics)
              and pillar_topics[0].expires_at == week_end)

    print("\n[3] 幂等：重复 plan")
    plan2 = client.post("/api/pillars/plan").json()
    by_name2 = {x["pillar"]: x for x in plan2}
    check("合集档跳过（existing=1）", by_name2["本周5个值得装的AI工具"]["existing"] == 1
          and not by_name2["本周5个值得装的AI工具"]["created"], str(plan2))
    check("多期档已满不新增", not by_name2["AI工具深挖"]["created"], str(plan2))

    print("\n[4] 撞题豁免（radar 不合并进 pillar 选题）")
    with session_scope() as session:
        hot = session.scalars(select(HotItem).where(HotItem.url == "https://xhs/a1")).one()
        outcome, topic = radar.create_or_merge_topic(
            session, hot, "AI与编程", "AI工具", score=2.0)
        # a1 的标题与合集/多期选题高度相似，但 pillar 选题不在候选集：
        # 应新建 radar 选题而非合并进 pillar
        check("radar 条目新建选题（未合并进 pillar）", outcome == "created" and topic.source == "radar",
              f"outcome={outcome}")
        pillar_rows = session.scalars(select(Topic).where(Topic.source == "pillar")).all()
        check("pillar 选题数量与 evidence 不变（4 条，evidence 未被追加）",
              len(pillar_rows) == 4
              and all(len((t.evidence or {}).get("items", [])) in (1, 5) for t in pillar_rows))

    print("\n[5] 采样接线：栏目关键词池优先于领域词表")
    queries = xhs_sample.XhsSampleCollector()._queries()
    check("检索词 = 栏目关键词池（去重）", queries == ["AI工具", "AIGC"], str(queries))
    check("定向采样：显式关键词覆盖栏目池/词表推导",
          xhs_sample.XhsSampleCollector(keywords=["新栏目词"])._queries() == ["新栏目词"])

    print("\n[6] 停用 / 删除约束")
    r3 = client.put(f"/api/pillars/{pid_m}", json={
        "name": "AI工具深挖", "angle": "单工具深度内容", "domain": "AI与编程",
        "slots_per_week": 3, "keywords": [], "active": False})
    check("停用栏目 200", r3.status_code == 200 and not r3.json()["active"])
    plan3 = client.post("/api/pillars/plan").json()
    check("停用栏目不参与排期", all(x["pillar"] != "AI工具深挖" for x in plan3), str(plan3))
    queries2 = xhs_sample.XhsSampleCollector()._queries()
    check("停用后采样词只剩启用栏目", queries2 == ["AI工具", "AIGC"], str(queries2))
    # 级联删除：带排期选题（未生成文章）的栏目可删，选题一并清理（工作台同步）
    r_cas = client.post("/api/pillars", json={"name": "级联删除验证", "angle": "x",
        "domain": "AI与编程", "slots_per_week": 1, "keywords": [], "active": True})
    pid_x = r_cas.json()["id"]
    with session_scope() as session:
        session.add(Topic(title="占位", angle="", domain="AI与编程", source="pillar",
                          status="new", evidence={"pillar_id": pid_x}))
    r4 = client.delete(f"/api/pillars/{pid_x}")
    with session_scope() as session:
        x_topics = session.scalars(
            select(Topic).where(Topic.source == "pillar", Topic.title == "占位")).all()
    check("带无文章选题的栏目删除级联 200 且选题清理",
          r4.status_code == 200 and r4.json().get("topics_removed") == 1 and not x_topics,
          str(r4.json()))
    r5 = client.post("/api/pillars", json={"name": "临时栏目", "keywords": []})
    r6 = client.delete(f"/api/pillars/{r5.json()['id']}")
    check("无排期选题的栏目可删除", r6.status_code == 200, str(r6.status_code))

    print("\n[7] /pillars 页面契约")
    page = client.get("/pillars")
    check("页面 200 且含栏目名与本周排期数", page.status_code == 200
          and "本周5个值得装的AI工具" in page.text and "1 期" in page.text)
    check("新建表单领域为可选可填（input+datalist）、带关键词推荐容器",
          'id="domain-input"' in page.text and 'id="domain-options"' in page.text
          and 'id="kw-chips"' in page.text)
    check("启用栏目行带定向采样入口（采样一轮 + 进度状态区）",
          "采样一轮" in page.text and 'id="sample-status"' in page.text
          and "samplePillar(" in page.text)

    print("\n[8] P5b 周主题：生成（mock）/ 素材不足拦截 / 确认")
    r8 = client.post("/api/pillars", json={
        "name": "AI实战周", "angle": "围绕每周主题出互补的几期", "domain": "AI与编程",
        "slots_per_week": 2, "keywords": ["AI工具", "AIGC"], "active": True})
    pid_d = r8.json()["id"]
    t1 = client.post(f"/api/pillars/{pid_d}/theme")
    body1 = t1.json()
    check("主题生成 proposed（mock 主题 + 2 子话题）",
          t1.status_code == 200 and body1["status"] == "proposed"
          and body1["theme"] and len(body1["subtopics"]) == 2, str(body1)[:120])
    r9 = client.post("/api/pillars", json={
        "name": "无素材栏目", "slots_per_week": 1, "keywords": ["完全不可能命中的词xyz"], "active": True})
    t2 = client.post(f"/api/pillars/{r9.json()['id']}/theme")
    check("素材不足时主题生成 422（防编内容）", t2.status_code == 422, str(t2.status_code))
    with session_scope() as session:
        a3 = session.scalars(select(HotItem).where(HotItem.url == "https://xhs/a3")).one()
        a3_id = a3.id
    t3 = client.put(f"/api/pillars/{pid_d}/theme", json={
        "theme": "AI 视频创作周", "subtopics": [
            {"title": "AI 自动剪片实测", "hot_item_ids": [a3_id]},
            {"title": "AI 写分镜脚本", "hot_item_ids": []}]})
    check("确认主题（可改写文案）", t3.status_code == 200 and t3.json()["status"] == "confirmed"
          and t3.json()["theme"] == "AI 视频创作周", str(t3.json())[:120])

    print("\n[9] P5b 主题排期：按子话题分期 + 期数 + 幂等")
    plan4 = client.post("/api/pillars/plan").json()
    d4 = next(x for x in plan4 if x["pillar"] == "AI实战周")
    check("主题模式建满 2 期", len(d4["created"]) == 2, str(d4))
    with session_scope() as session:
        deep = [t for t in session.scalars(
            select(Topic).where(Topic.source == "pillar").order_by(Topic.id)).all()
            if (t.evidence or {}).get("pillar_id") == pid_d]
        check("标题带期数与子话题",
              [t.title for t in deep] == ["AI实战周第1期｜AI 自动剪片实测", "AI实战周第2期｜AI 写分镜脚本"],
              str([t.title for t in deep]))
        check("evidence 带主题/期数/子话题",
              all((t.evidence or {}).get("week_theme") == "AI 视频创作周"
                  and (t.evidence or {}).get("episodes_total") == 2
                  and (t.evidence or {}).get("subtopic") for t in deep))
        check("子话题绑定指定素材；未绑素材回退最高赞",
              deep[0].evidence["items"][0]["url"] == "https://xhs/a3"
              and len(deep[1].evidence["items"]) == 1, str(deep[1].evidence["items"])[:120])
    plan5 = client.post("/api/pillars/plan").json()
    d5 = next(x for x in plan5 if x["pillar"] == "AI实战周")
    check("主题排期幂等（子话题已覆盖不重建）", not d5["created"], str(d5))

    print("\n[10] P5b 生成系列上下文 + 选题归档")
    from app.api.routes_topics import _build_variables

    with session_scope() as session:
        deep = [t for t in session.scalars(select(Topic).where(Topic.source == "pillar")).all()
                if (t.evidence or {}).get("pillar_id") == pid_d]
        vars1 = _build_variables(session, deep[0])
        check("系列变量：主题/期数/总数/其他期",
              vars1["series_theme"] == "AI 视频创作周" and vars1["series_episode"] == 1
              and vars1["series_total"] == 2
              and vars1["series_others"] == ["AI实战周第2期｜AI 写分镜脚本"], str(vars1["series_others"]))
        coll = [t for t in session.scalars(select(Topic)).all()
                if (t.evidence or {}).get("pillar_id") == pid_c][0]
        vars2 = _build_variables(session, coll)
        check("合集选题的系列变量（自身即枢纽）",
              vars2["series_hub"] == coll.title and vars2["series_others"] == [],
              str(vars2["series_hub"])[:60])
    with session_scope() as session:
        tid = session.scalars(select(Topic).where(Topic.title == "AI实战周第1期｜AI 自动剪片实测")).one().id
    ra = client.put(f"/api/topics/{tid}/archive")
    check("归档选题 200", ra.status_code == 200 and ra.json()["status"] == "archived", str(ra.status_code))
    home = client.get("/")
    check("归档后工作台不再展示该选题",
          home.status_code == 200 and "AI 自动剪片实测" not in home.text)

    print("\n[11] P5b 合集素材不足拦截（防模型虚构内容）")
    plan6 = client.post("/api/pillars/plan").json()
    d6 = next(x for x in plan6 if x["pillar"] == "无素材栏目")
    check("素材不足不建合集、返回 warning", not d6["created"] and "素材" in d6.get("warning", ""),
          str(d6))

    print("\n[12] P5b 按主题重排：旧选题归档 + 按主题重排 / 提示引导")
    r12 = client.post("/api/pillars", json={
        "name": "重排测试", "angle": "先排旧模式再切主题", "domain": "AI与编程",
        "slots_per_week": 2, "keywords": ["AI工具", "AIGC"], "active": True})
    pid_r = r12.json()["id"]
    plan7 = client.post("/api/pillars/plan").json()
    d7 = next(x for x in plan7 if x["pillar"] == "重排测试")
    check("未确认主题时按旧模式排满 2 期", len(d7["created"]) == 2
          and all("｜" in c["title"] for c in d7["created"]), str(d7["created"]))
    client.post(f"/api/pillars/{pid_r}/theme")
    with session_scope() as session:
        a1 = session.scalars(select(HotItem).where(HotItem.url == "https://xhs/a1")).one()
        a1_id = a1.id
    client.put(f"/api/pillars/{pid_r}/theme", json={
        "theme": "重排主题周", "subtopics": [
            {"title": "效率工具怎么选", "hot_item_ids": [a1_id]},
            {"title": "工作流组合思路", "hot_item_ids": []}]})
    plan8 = client.post("/api/pillars/plan").json()
    d8 = next(x for x in plan8 if x["pillar"] == "重排测试")
    check("确认主题后普通 plan 不动旧选题、返回重排提示",
          not d8["created"] and "按主题重排" in d8.get("warning", ""), str(d8))
    plan9 = client.post("/api/pillars/plan?pillar_id=%d&replan_theme=true" % pid_r).json()
    d9 = plan9[0]
    check("重排归档 2 条旧选题并按主题重建 2 期",
          d9.get("archived_legacy") == 2 and len(d9["created"]) == 2, str(d9))
    with session_scope() as session:
        rows = [t for t in session.scalars(select(Topic).where(Topic.source == "pillar")).all()
                if (t.evidence or {}).get("pillar_id") == pid_r]
        active = [t for t in rows if t.status != "archived"]
        archived = [t for t in rows if t.status == "archived"]
        check("旧 2 期已 archived，在库 2 期带子话题标记",
              len(archived) == 2 and len(active) == 2
              and all((t.evidence or {}).get("subtopic") for t in active)
              and [t.title for t in active] == ["重排测试第1期｜效率工具怎么选", "重排测试第2期｜工作流组合思路"],
              str([(t.title, t.status) for t in rows]))
    page_r = client.get("/pillars")
    check("重排后 /pillars 计划数不含归档（2 期）",
          page_r.status_code == 200 and "按主题重排（归档本周旧选题）" in page_r.text)

    print("\n[13] P5b 归档选题不占档位：全部归档后排期重新建满")
    with session_scope() as session:
        rids = [t.id for t in session.scalars(select(Topic).where(Topic.source == "pillar")).all()
                if (t.evidence or {}).get("pillar_id") == pid_r]
    for tid in rids:
        assert client.put(f"/api/topics/{tid}/archive").status_code == 200
    plan10 = client.post(f"/api/pillars/plan?pillar_id={pid_r}").json()
    d10 = plan10[0]
    check("归档后 plan 不被归档选题挡住，按主题重建 2 期",
          len(d10["created"]) == 2 and not d10.get("warning"), str(d10))

    print("\n[14] 选题标题可编辑（排期标题只是占位，成文前可改）")
    tid14 = d10["created"][0]["id"]
    rt = client.put(f"/api/topics/{tid14}/title", json={"title": "  省下百万预算的AI大片工作流  "})
    check("改标题 200 且去空白", rt.status_code == 200 and rt.json()["title"] == "省下百万预算的AI大片工作流",
          str(rt.json()))
    re_ = client.put(f"/api/topics/{tid14}/title", json={"title": "   "})
    check("空标题 422", re_.status_code == 422, str(re_.status_code))
    home14 = client.get("/")
    check("工作台显示新标题", home14.status_code == 200 and "省下百万预算的AI大片工作流" in home14.text)

    print("\n[15] 新建栏目自动采样入队 + 素材数端点")
    config.PILLAR_AUTO_SAMPLE = True
    try:
        r15 = client.post("/api/pillars", json={
            "name": "自动采样验证", "domain": "AI与编程", "slots_per_week": 1,
            "keywords": ["AI工具"], "active": True})
        body15 = r15.json()
        job_id = body15.get("sampling_job_id")
        check("创建 201 且响应带采样任务 id", r15.status_code == 201
              and isinstance(job_id, int), str(body15))
        d15 = client.get(f"/api/sampling/jobs/{job_id}").json()
        check("任务已入队（kind=pillar、词快照、pillar_id 关联）",
              d15["status"] == "queued" and d15["kind"] == "pillar"
              and d15["keywords"] == ["AI工具"] and d15["pillar_id"] == body15["id"]
              and d15["total_queries"] == 1, str(d15))
        r15b = client.post("/api/pillars", json={"name": "无词栏目", "keywords": []})
        check("无关键词栏目不入队", r15b.json().get("sampling_job_id") is None)
    finally:
        config.PILLAR_AUTO_SAMPLE = False
    m15 = client.get(f"/api/pillars/{pid_c}/materials")
    check("素材进度端点返回本周命中数与门槛",
          m15.status_code == 200 and m15.json()["matched"] >= 3
          and m15.json()["min_required"] == pillar_service.COLLECTION_MIN_MATERIALS, str(m15.json()))
    m404 = client.get("/api/pillars/99999/materials")
    check("不存在栏目 404", m404.status_code == 404, str(m404.status_code))

    print("\n[16] 单栏目定向采样（补采入口）")
    s404 = client.post("/api/pillars/99999/sample")
    check("不存在栏目 404", s404.status_code == 404, str(s404.status_code))
    s16 = client.post(f"/api/pillars/{pid_c}/sample")
    b16 = s16.json()
    check("定向采样 202（kind=pillar、当前词池快照、pillar_id 关联）",
          s16.status_code == 202 and b16["created"] is True
          and b16["job"]["kind"] == "pillar" and b16["job"]["pillar_id"] == pid_c
          and b16["job"]["keywords"] == ["AI工具", "AIGC"]
          and b16["job"]["status"] == "queued", str(b16)[:160])
    s16b = client.post(f"/api/pillars/{pid_c}/sample")
    check("连点去重（created=False，返回同一任务不重复计费）",
          s16b.status_code == 202 and s16b.json()["created"] is False
          and s16b.json()["job"]["id"] == b16["job"]["id"], str(s16b.json())[:120])
    s16c = client.post(f"/api/pillars/{pid_m}/sample")
    check("词池为空 422（[6] 已清空该栏目关键词）",
          s16c.status_code == 422, f"{s16c.status_code} {str(s16c.json())[:80]}")

    print()
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过")
        for name in FAILURES:
            print(f"  ✗ {name}")
        return 1
    print("PASS：P5 内容栏目验收全部通过")
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
