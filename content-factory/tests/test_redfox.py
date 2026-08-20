#!/usr/bin/env python
"""RedFox 数据源验收脚本（可重复运行，不依赖外网与 Key）。

覆盖：
  1. _to_int 容错（int / "1,200" / "1.2万" / "5w+" / None）
  2. _unwrap：{code:2000,data} 包装 / 顶层裸响应 / 业务码非 2000 报错
  3. 洞察解析：articles → HotItem 字段映射；latestHotArticles 兜底不采；
     无标题跳过；URL 缺省按笔记 ID 构造
  4. work_detail：包装解包；空参报错
  5. 单源调度：配 Key 走 RedFox；RedFox 失败直接抛错（无降级源）；
     无 Key 立即报错（不联网）
  6. probe_fans：RedFox 失败时返回带 error 的结构（不抛异常、不误报结论）
  7. 违禁词检测：HTML 标注提取命中词（保序去重）/ 分类过滤空项 / 业务码
     报错 / 无 Key 不联网 / 超 3000 字拒绝

运行：.venv/Scripts/python tests/test_redfox.py
"""
import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.collectors import redfox  # noqa: E402
from app.collectors import xhs_sample  # noqa: E402
from app.schemas import HotItem as HotItemSchema  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


# 文档示例响应（小红书爆款笔记洞察，包装形态 + latestHotArticles 兜底）
_INSIGHT_WRAPPED = {
    "code": 2000,
    "msg": "成功",
    "data": {
        "articles": [
            {
                "authorFans": 8614,
                "authorId": "66b81a14000000001d020e53",
                "authorNickname": "飞飞在学AI",
                "collectedCount": 1120,
                "commentsCount": 26,
                "createTime": "2026-07-08 13:05:29",
                "id": "6a4dcb110000000016025b73",
                "interactiveCount": 1775,
                "likedCount": 629,
                "shareInfoLink": "https://www.xiaohongshu.com/explore/6a4dcb110000000016025b73",
                "sharedCount": 228,
                "title": "从0到上线：用Fable 5搭一个投研Agent",
                "totalScore": 7.5,
            },
            {  # 无标题 → 解析时跳过
                "authorFans": 288, "authorNickname": "无名",
                "likedCount": 100, "id": "notitle1",
            },
        ],
        "latestHotArticles": [  # 全站兜底推荐，与检索词无关 → 必须丢弃
            {"authorFans": 319414, "authorNickname": "兜底作者", "likedCount": 623922,
             "id": "junk1", "title": "T恤的后面比前面好看怎么办？"},
        ],
        "total": 2,
    },
}

# 文档示例响应（顶层裸给、无包装的形态）
_INSIGHT_BARE = {
    "articles": [
        {
            "authorFans": 288,
            "authorNickname": "broisnotacat",
            "collectedCount": 77357,
            "commentsCount": 29290,
            "id": "6a75865e0000000006006baa",
            "likedCount": 889107,
            "sharedCount": 230074,
            "title": "让我们一起谢谢小猫！",
            # 无 shareInfoLink → URL 按笔记 ID 构造
        },
    ],
    "total": 1,
}

_DETAIL_WRAPPED = {
    "code": 2000,
    "msg": "成功",
    "data": {
        "workId": "687df3a1000000000d0184a4",
        "workTitle": "建筑师的选择｜厨下净水器终于装好了！",
        "workDesc": "做了十多年建筑设计……",
        "accountNickname": "大白萝不怪",
        "workLikedCount": 210,
        "workCollectedCount": 175,
        "workCommentsCount": 45,
        "workReadedCount": 980,
    },
}


def main() -> int:
    print("[1] _to_int 容错解析")
    check("int 直通", redfox._to_int(8614) == 8614)
    check("'1,200'→1200", redfox._to_int("1,200") == 1200)
    check("'1.2万'→12000", redfox._to_int("1.2万") == 12000)
    check("'5w+'→50000（七日榜模糊量级按下界）", redfox._to_int("5w+") == 50000)
    check("None→0", redfox._to_int(None) == 0)

    print("\n[2] _unwrap 响应包装兼容")
    check("code=2000 → 解包 data", redfox._unwrap({"code": 2000, "data": {"x": 1}}, "t") == {"x": 1})
    check("顶层裸响应原样返回", redfox._unwrap({"articles": []}, "t") == {"articles": []})
    try:
        redfox._unwrap({"code": 4001, "msg": "余额不足"}, "t")
        check("code≠2000 抛 RedFoxError", False)
    except redfox.RedFoxError as exc:
        check("code≠2000 抛 RedFoxError", "4001" in str(exc), str(exc))

    print("\n[3] 洞察搜索与解析")
    real_post = redfox._post

    def fake_post(path, payload):
        assert path == redfox.INSIGHT_PATH, path
        assert payload["keyword"] == "AI工具", payload
        assert payload["startDate"] and payload["endDate"], payload
        return json.loads(json.dumps(_INSIGHT_WRAPPED))

    redfox._post = fake_post
    try:
        articles = redfox.search_articles("AI工具")
        check("只取 articles（latestHotArticles 兜底丢弃）",
              len(articles) == 2 and {a["id"] for a in articles} == {
                  "6a4dcb110000000016025b73", "notitle1"}, f"len={len(articles)}")
        items = redfox.parse_articles(articles)
        check("无标题条目跳过 → 1 条 HotItem", len(items) == 1 and isinstance(items[0], HotItemSchema))
        it = items[0]
        check("字段映射 fans/likes/collects/comments",
              (it.fans, it.likes, it.collects, it.comments) == (8614, 629, 1120, 26),
              f"{(it.fans, it.likes, it.collects, it.comments)}")
        check("author / url 取 shareInfoLink", it.author == "飞飞在学AI"
              and it.url.endswith("/explore/6a4dcb110000000016025b73"), it.url or "")
        check("source=xhs 且 raw 保留原文", it.source == "xhs" and it.raw["article"]["totalScore"] == 7.5)

        redfox._post = lambda path, payload: json.loads(json.dumps(_INSIGHT_BARE))
        bare_items = redfox.search_hot_items("AI工具")
        check("顶层裸响应同样可解析", len(bare_items) == 1 and bare_items[0].fans == 288)
        check("URL 缺省按笔记 ID 构造",
              bare_items[0].url.endswith("/explore/6a75865e0000000006006baa"), bare_items[0].url)

        redfox._post = lambda path, payload: {"code": 2000, "data": {"total": 0}}
        try:
            redfox.search_articles("AI工具")
            check("缺 articles 列表抛 RedFoxError", False)
        except redfox.RedFoxError:
            check("缺 articles 列表抛 RedFoxError", True)
    finally:
        redfox._post = real_post

    print("\n[4] work_detail")
    redfox._post = lambda path, payload: (json.loads(json.dumps(_DETAIL_WRAPPED))
                                          if payload.get("workId") else {})
    try:
        detail = redfox.work_detail(work_id="687df3a1000000000d0184a4")
        check("包装解包 + 字段齐全", detail["workReadedCount"] == 980 and detail["workTitle"])
        redfox._post = real_post
        try:
            redfox.work_detail()
            check("空参抛 RedFoxError", False)
        except redfox.RedFoxError:
            check("空参抛 RedFoxError", True)
    finally:
        redfox._post = real_post

    print("\n[5] 单源调度（fetch 走 RedFox；失败/无 Key 直接抛错，无降级源）")
    saved_key = config.REDFOX_API_KEY
    saved_enabled = xhs_sample.redfox_enabled
    saved_search_hot = xhs_sample.search_hot_items
    collector = xhs_sample.XhsSampleCollector()
    try:
        # 5a. 配 Key → 走 RedFox
        redfox_items = [HotItemSchema(source="xhs", title="redfox条目", fans=100)]
        xhs_sample.redfox_enabled = lambda: True  # 强制视为已启用，验证采样分支
        xhs_sample.search_hot_items = lambda kw: redfox_items
        got, src = collector.fetch_keyword("AI工具")
        check("RedFox 可用 → 采样走 redfox 源", src == "redfox" and got is redfox_items)

        # 5b. RedFox 报错 → 异常直接冒泡（mcp 降级源已废弃，由调用方计熔断）
        def boom(kw):
            raise redfox.RedFoxError("余额不足")
        xhs_sample.search_hot_items = boom
        try:
            collector.fetch_keyword("AI工具")
            check("RedFox 失败 → 直接抛 RedFoxError（无降级）", False)
        except redfox.RedFoxError as exc:
            check("RedFox 失败 → 直接抛 RedFoxError（无降级）", "余额不足" in str(exc), str(exc))

        # 5c. enabled() 真实读 config：无 Key 时立即报错且不联网
        config.REDFOX_API_KEY = ""
        xhs_sample.redfox_enabled = saved_enabled  # 恢复真实实现
        xhs_sample.search_hot_items = lambda kw: (_ for _ in ()).throw(AssertionError("不应调用 redfox"))
        try:
            collector.fetch_keyword("AI工具")
            check("无 Key → 立即抛 RedFoxError 且不联网", False)
        except redfox.RedFoxError as exc:
            check("无 Key → 立即抛 RedFoxError 且不联网", "未配置" in str(exc), str(exc))
    finally:
        config.REDFOX_API_KEY = saved_key
        xhs_sample.redfox_enabled = saved_enabled
        xhs_sample.search_hot_items = saved_search_hot

    print("\n[6] probe_fans（RedFox 失败不抛异常、结论不误报）")
    saved_probe = xhs_sample.redfox_probe
    try:
        config.REDFOX_API_KEY = "ak_test"
        xhs_sample.redfox_enabled = lambda: True

        def probe_boom(kw):
            raise redfox.RedFoxError("HTTP 401：鉴权失败")
        xhs_sample.redfox_probe = probe_boom
        result = xhs_sample.probe_fans("AI工具")
        check("失败返回 error 结构", result.get("source") == "redfox" and "error" in result)
        check("结论指向排查而非降级模式", "RedFox 调用失败" in result.get("conclusion", ""))

        xhs_sample.redfox_probe = lambda kw: {"source": "redfox", "fans_available": True,
                                              "note_count": 5, "fans_found": 5, "conclusion": "ok"}
        result = xhs_sample.probe_fans("AI工具")
        check("成功透传 redfox 探针结果", result["fans_available"] is True and result["source"] == "redfox")

        # 无 Key → 返回配 Key 提示，不调 RedFox（探针也单源化）
        config.REDFOX_API_KEY = ""
        xhs_sample.redfox_enabled = saved_enabled
        xhs_sample.redfox_probe = lambda kw: (_ for _ in ()).throw(AssertionError("不应调用 redfox"))
        result = xhs_sample.probe_fans("AI工具")
        check("无 Key → 返回配 Key 提示（不调 RedFox）",
              "REDFOX_API_KEY" in result.get("conclusion", ""), str(result.get("conclusion")))
    finally:
        config.REDFOX_API_KEY = saved_key
        xhs_sample.redfox_enabled = saved_enabled
        xhs_sample.redfox_probe = saved_probe

    print("\n[7] 违禁词检测（发布前体检，X-API-KEY 头 + HTML 标注解析）")
    saved_key = config.REDFOX_API_KEY
    real_post = redfox._post
    try:
        config.REDFOX_API_KEY = "ak_test"

        def fake_sensitive_post(path, payload, extra_headers=None):
            assert path == redfox.SENSITIVE_PATH, path
            assert payload["platform"] == "小红书" and payload["content"], payload
            assert extra_headers and extra_headers.get("X-API-KEY") == "ak_test", extra_headers
            return {
                "code": 2000, "msg": "成功",
                "data": {
                    "content": "加微信<span class=\"banned-word\">免费领</span>课程，"
                               "<span class=\"sensitive-word\">限时</span>优惠，"
                               "<span class=\"banned-word\">免费领</span>私信，"
                               "用 <span class=\"banned-word\">av</span> 与 <span class=\"banned-word\">free</span>",
                    "originalContent": "加微信免费领课程，限时优惠，免费领私信，用 Gravitas 与 free",
                    "prohibitedWordsType": ["诱导行为", "  ", ""],
                },
            }

        redfox._post = fake_sensitive_post
        result = redfox.sensitive_word_search("加微信免费领课程，限时优惠，免费领私信，用 Gravitas 与 free")
        check("命中词提取（保序去重）", result["words"] == ["免费领", "限时", "free"], str(result["words"]))
        check("英文子串误报剔除（av ⊂ Gravitas，standalone free 保留）",
              "av" not in result["words"] and "free" in result["words"], str(result["words"]))
        check("风险分类过滤空项", result["categories"] == ["诱导行为"], str(result["categories"]))

        redfox._post = lambda path, payload, extra_headers=None: {"code": 4001, "msg": "余额不足"}
        try:
            redfox.sensitive_word_search("内容")
            check("业务码非 2000 抛 RedFoxError", False)
        except redfox.RedFoxError as exc:
            check("业务码非 2000 抛 RedFoxError", "4001" in str(exc), str(exc))

        config.REDFOX_API_KEY = ""
        try:
            redfox.sensitive_word_search("内容")
            check("无 Key 抛 RedFoxError（不联网）", False)
        except redfox.RedFoxError as exc:
            check("无 Key 抛 RedFoxError（不联网）", "未配置" in str(exc), str(exc))

        config.REDFOX_API_KEY = "ak_test"
        try:
            redfox.sensitive_word_search("字" * 3001)
            check("超 3000 字拒绝（不调接口）", False)
        except redfox.RedFoxError as exc:
            check("超 3000 字拒绝（不调接口）", "3001" in str(exc), str(exc))
    finally:
        config.REDFOX_API_KEY = saved_key
        redfox._post = real_post

    print()
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过")
        for name in FAILURES:
            print(f"  ✗ {name}")
        return 1
    print("PASS：RedFox 数据源验收全部通过")
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
