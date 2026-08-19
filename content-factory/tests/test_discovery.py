#!/usr/bin/env python
"""P5d 领域发现验收脚本（可重复运行，不依赖外网与真实 Key）。

覆盖：
  1. RedFox 新接口封装：七日爆款（GET/包装解包/形状校验）+ 账号搜索（list 提取）
  2. 关键词挖掘：hashtag 词频排序；hashtag 不足时标题二元组补位
  3. category_insights 缓存：同类目第二次调用不再打 RedFox（不重复计费）
  4. register_domain：新领域登记 / 已有领域只追加缺失词 / 无变化不写盘
  5. API：/api/discovery/categories（官方 24 类 + 自定义分组）、
     keyword-ideas、benchmark-accounts；未配 Key 时 503 明确告知
  6. 建栏目自动注册：官方类目 + 栏目关键词进词表（入库过滤可命中）；
     自定义领域不动词表

运行：.venv/Scripts/python tests/test_discovery.py
"""
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="p5d_check_"))
config.DB_PATH = _TMP / "app.db"
config.BACKUP_DIR = _TMP / "backups"
config.DOMAINS_FILE = _TMP / "domains.yml"
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""
config.LLM_MOCK = True
config.PILLAR_AUTO_SAMPLE = False  # 不触发真实采样
config.REDFOX_API_KEY = "test-key"  # discovery_ready() 需要；HTTP 层全部打桩

from fastapi.testclient import TestClient  # noqa: E402

from app.collectors import redfox  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import radar, xhs_discovery  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


# ---- 文档示例响应 ----
_SEVEN_WRAPPED = {
    "code": 2000, "msg": "成功",
    "data": [
        {"title": "考研英语保姆级规划", "desc": "#考研 #英语学习 干货",
         "photoJumpUrl": "https://www.xiaohongshu.com/explore/n1", "publicTime": "2026-08-15 10:00:00",
         "anaAdd": {"addLikeCount": "36w+", "useLikeCount": "38w+"}},
        {"title": "英语学习避坑指南", "desc": "#英语学习 #考研",
         "photoJumpUrl": "https://www.xiaohongshu.com/explore/n2", "publicTime": "2026-08-16 11:00:00",
         "anaAdd": {"addLikeCount": "5w+", "useLikeCount": "6w+"}},
        {"title": "", "desc": "#高考", "anaAdd": {"addLikeCount": "9w+"}},  # 空标题 → 示例列表跳过
        "不是字典",  # 非对象条目 → 跳过
    ],
}
_ACCOUNTS_WRAPPED = {
    "code": 2000, "msg": "成功",
    "data": {"total": 2, "hasMore": False, "list": [
        {"accountName": "考研规划师", "accountFans": 330558, "accountTotalWorks": 44,
         "accountLikes": 429909, "accountDesc": "专注考研规划\n保姆级教程"},
        {"accountName": "", "accountFans": 1},  # 无名条目 → 过滤
    ]},
}


def main() -> int:
    print(f"临时工作目录：{_TMP}")
    config.DOMAINS_FILE.write_text(
        "domains:\n  AI与编程:\n    keywords: [AI, 编程]\n", encoding="utf-8"
    )
    init_db()
    client = TestClient(app)

    print("\n[1] RedFox 新接口封装")
    real_get, real_post = redfox._get, redfox._post
    redfox._get = lambda path, params: _SEVEN_WRAPPED
    notes = redfox.seven_day_hot("学习教育")
    check("七日爆款解包出 3 条笔记（非对象条目跳过）",
          len(notes) == 3 and notes[0]["title"].startswith("考研"))
    redfox._get = lambda path, params: {"code": 2000, "data": {"oops": 1}}
    try:
        redfox.seven_day_hot("学习教育")
        check("七日爆款形状异常抛 RedFoxError", False)
    except redfox.RedFoxError:
        check("七日爆款形状异常抛 RedFoxError", True)
    captured = {}
    def fake_post(path, payload):
        captured.update(payload)
        return _ACCOUNTS_WRAPPED
    redfox._post = fake_post
    accounts = redfox.search_accounts("考研")
    check("账号搜索提取 list 且默认最热排序",
          len(accounts) == 2 and accounts[0]["accountName"] == "考研规划师"
          and captured.get("sortType") == "_4", str(captured))
    redfox._get, redfox._post = real_get, real_post

    print("\n[2] 关键词挖掘")
    kws = xhs_discovery.mine_keywords(_SEVEN_WRAPPED["data"])
    check("hashtag 按词频排序（考研/英语学习居前）",
          kws[:2] == ["考研", "英语学习"] or kws[:2] == ["英语学习", "考研"], str(kws))
    no_tag_notes = [{"title": "大厂裁员风口来了真的猛", "desc": ""},
                    {"title": "大厂裁员的风口真的猛", "desc": ""}]
    kws2 = xhs_discovery.mine_keywords(no_tag_notes)
    check("无 hashtag 时标题高频二元组补位", "大厂" in kws2 and "裁员" in kws2, str(kws2))

    print("\n[3] category_insights 缓存（不重复计费）")
    xhs_discovery._CACHE.clear()
    calls = {"n": 0}
    real_seven = redfox.seven_day_hot
    redfox.seven_day_hot = lambda category: (calls.__setitem__("n", calls["n"] + 1), _SEVEN_WRAPPED["data"])[1]
    try:
        first = xhs_discovery.category_insights("学习教育")
        second = xhs_discovery.category_insights("学习教育")
        check("两次调用只打 1 次 RedFox", calls["n"] == 1, str(calls))
        check("返回推荐词 + 爆款标题示例（点赞倒序）",
              first["keywords"] and first["notes"][0]["likes"] >= first["notes"][1]["likes"]
              and first["notes"][0]["likes"] == 360000, str(first["notes"]))
        check("第二次命中缓存且结果一致", second == first)
    finally:
        redfox.seven_day_hot = real_seven
        xhs_discovery._CACHE.clear()

    print("\n[4] register_domain 词表登记")
    wrote = radar.register_domain("学习教育", ["考研", "考研英语"])
    check("新领域登记写入", wrote and "学习教育" in radar.load_domains())
    wrote2 = radar.register_domain("学习教育", ["考研"])  # 全是已有词
    check("无新词不写盘", not wrote2)
    wrote3 = radar.register_domain("学习教育", ["考研", "四六级"])
    domains = radar.load_domains()
    check("已有领域只追加缺失词（顺序保持）",
          wrote3 and domains["学习教育"] == ["考研", "考研英语", "四六级"], str(domains.get("学习教育")))
    check("原有领域不受影响", domains["AI与编程"] == ["AI", "编程"])

    print("\n[5] /api/discovery 端点")
    r_cat = client.get("/api/discovery/categories")
    body = r_cat.json()
    check("categories：官方 24 类 + 自定义分组排除官方",
          r_cat.status_code == 200 and len(body["official"]) == 24
          and "学习教育" in body["official"] and body["custom"] == ["AI与编程"], str(body["custom"]))
    real_seven = redfox.seven_day_hot
    redfox.seven_day_hot = lambda category: _SEVEN_WRAPPED["data"]
    try:
        r_ideas = client.get("/api/discovery/keyword-ideas", params={"category": "学习教育"})
        d = r_ideas.json()
        check("keyword-ideas 返回推荐词与示例",
              r_ideas.status_code == 200 and d["keywords"] and len(d["notes"]) == 2, str(d)[:100])
    finally:
        redfox.seven_day_hot = real_seven
        xhs_discovery._CACHE.clear()
    real_search = redfox.search_accounts
    redfox.search_accounts = lambda keyword, offset=0, sort_type="_4": _ACCOUNTS_WRAPPED["data"]["list"]
    try:
        r_acc = client.get("/api/discovery/benchmark-accounts", params={"keyword": "考研"})
        d = r_acc.json()
        check("benchmark-accounts 过滤无名账号",
              r_acc.status_code == 200 and len(d["accounts"]) == 1
              and d["accounts"][0]["fans"] == 330558, str(d["accounts"]))
    finally:
        redfox.search_accounts = real_search
        xhs_discovery._CACHE.clear()
    no_key = config.REDFOX_API_KEY
    config.REDFOX_API_KEY = ""
    try:
        r503 = client.get("/api/discovery/keyword-ideas", params={"category": "学习教育"})
        check("未配 Key 时 503 且提示可手填", r503.status_code == 503 and "手动填" in r503.json()["detail"])
    finally:
        config.REDFOX_API_KEY = no_key

    print("\n[6] 建栏目自动注册领域（官方类目 + 手填新领域）")
    before = radar.load_domains()
    r_p = client.post("/api/pillars", json={
        "name": "学习教育测试", "domain": "学习教育", "slots_per_week": 1,
        "keywords": ["考研", "雅思备考"], "active": True})
    after = radar.load_domains()
    check("官方类目建栏目 → 栏目关键词合并进词表",
          r_p.status_code == 201 and "雅思备考" in after.get("学习教育", []),
          str(after.get("学习教育")))
    r_p2 = client.post("/api/pillars", json={
        "name": "自定义领域测试", "domain": "AI与编程", "slots_per_week": 1,
        "keywords": ["AI"], "active": True})
    check("已有领域无新词不动词表", r_p2.status_code == 201 and radar.load_domains() == after)
    r_p3 = client.post("/api/pillars", json={
        "name": "手填新领域测试", "domain": "健身减脂", "slots_per_week": 1,
        "keywords": ["减脂餐", "体态矫正"], "active": True})
    check("手填新领域建栏目 → 连同关键词登记进词表",
          r_p3.status_code == 201 and radar.load_domains().get("健身减脂") == ["减脂餐", "体态矫正"],
          str(radar.load_domains().get("健身减脂")))

    print("\n[7] /pillars 与 /viral 表单契约")
    page = client.get("/pillars")
    check("领域可选可填（input+datalist），候选含官方类目与自定义领域",
          page.status_code == 200 and 'id="domain-input"' in page.text
          and 'id="domain-options"' in page.text and "学习教育" in page.text
          and "AI与编程" in page.text)
    check("获取推荐词 / 对标账号按钮就位",
          "获取推荐词" in page.text and "找对标账号" in page.text)
    viral_page = client.get("/viral")
    check("人工喂样本领域同步可选可填（官方类目进候选）",
          viral_page.status_code == 200 and 'id="f-domain"' in viral_page.text
          and 'list="f-domain-options"' in viral_page.text and "学习教育" in viral_page.text)

    print()
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过")
        for name in FAILURES:
            print(f"  ✗ {name}")
        return 1
    print("PASS：P5d 领域发现验收全部通过")
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
