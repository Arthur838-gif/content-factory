#!/usr/bin/env python
"""P1 验收脚本（可重复运行，不依赖外网与运行中的服务，无 Key 走 mock）。

覆盖任务四件套 P1 结构验收点：
  1. xhs+note+v1 种子模板入库（幂等，重启不覆盖）
  2. 端到端 mock 生成 platform=xhs：ready 行 + tags 数组 + meta.usage/cover_note/image_plan
  3. M7 文案适配：content 末尾拼 #标签（与 tags 字段一一对应、不含 # 号）
  4. mock 产物满足 P1 量规（结构可测部分：标题 emoji/字数、段落行数、字数区间、标签数、金句）
  5. 重新生成开新行 + 旧行归档（xhs）；published 终态 409（xhs）
  6. 敏感词命中（xhs 词表）→ status=failed + error 注明命中词
  7. P0 回归：wechat 生成链路不受影响；400 不支持平台
  8. 适配器单元：标签去重去 #、meta 字段映射

运行：.venv/Scripts/python tests/test_p1.py
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + mock 模式 + 不起调度器，确保离线可重复
_TMP = Path(tempfile.mkdtemp(prefix="p1_check_"))
config.DB_PATH = _TMP / "app.db"
config.SENSITIVE_FILE_WECHAT = _TMP / "sensitive_wechat.txt"
config.SENSITIVE_FILE_XHS = _TMP / "sensitive_xhs.txt"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""

from sqlalchemy import select  # noqa: E402

from app.adapters import xhs as xhs_adapter  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Article, Prompt, Topic  # noqa: E402
from app.schemas import XhsNote  # noqa: E402
from app.services import generator, prompt_engine  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def _has_emoji(s: str) -> bool:
    return any(
        0x1F000 <= ord(c) <= 0x1FAFF  # 表情符号区（🥲🔥💡👇）
        or 0x2600 <= ord(c) <= 0x27BF  # 杂项符号区
        or ord(c) == 0xFE0F  # 变体选择符（1️⃣ 等）
        for c in s
    )


def _insert_topics() -> None:
    with session_scope() as s:
        s.add(Topic(title="DeepSeek 发布新一代大模型，编程能力大幅提升", angle="AI·编程",
                    domain="AI与编程", source="radar", status="new", score=1.2,
                    evidence={"items": [{"title": "DeepSeek 新模型", "url": "https://x.com/hot/1"}]}))
        s.add(Topic(title="大模型价格战再起，开发者迎来红利", angle="AI·成本",
                    domain="AI与编程", source="radar", status="new", score=1.0))
        s.add(Topic(title="写公众号还有前途吗", angle="自媒体·趋势",
                    domain="自媒体", source="radar", status="new", score=0.8))


def _http_generate(client, topic_id: int, platform: str, prompt_id: int | None = None):
    url = f"/api/topics/{topic_id}/generate?platform={platform}"
    if prompt_id is not None:
        url += f"&prompt_id={prompt_id}"
    return client.post(url)


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    print(f"临时工作目录：{_TMP}")
    init_db()
    _insert_topics()

    print("\n[1] 种子模板入库（幂等键 xhs+note+v1）")
    seeded = prompt_engine.seed_prompts()
    check("xhs 模板首次入库", "xhs+note+v1" in seeded, str(seeded))
    check("wechat 模板同时入库（P0 回归）", "wechat+article+v1" in seeded, str(seeded))
    reseeded = prompt_engine.seed_prompts()
    check("重复入库跳过（重启不覆盖）", reseeded == [], str(reseeded))
    with session_scope() as s:
        p = s.scalars(select(Prompt).where(Prompt.platform == "xhs")).first()
        check("模板字段完整", p is not None and p.scenario == "note" and p.version == 1
              and p.enabled, str(p and p.scenario))
        check("模板含 system/user 标记与 tag_candidates 变量",
              p is not None and "# system" in p.template and "# user" in p.template
              and "tag_candidates" in p.template)
        pid = p.id if p else None

    print("\n[2] 端到端 mock 生成（platform=xhs）")
    client = TestClient(app)  # 不进 lifespan，避免起调度器；init_db 已手动完成
    resp = _http_generate(client, 1, platform="xhs")
    body = resp.json()
    check("HTTP 200", resp.status_code == 200, str(resp.status_code))
    check("返回 article_id 与 status=ready", body.get("status") == "ready"
          and isinstance(body.get("article_id"), int), str(body))
    aid1 = body["article_id"]
    with session_scope() as s:
        art = s.get(Article, aid1)
        check("articles 写 ready 行", art is not None and art.status == "ready", str(art and art.status))
        check("platform=xhs", art.platform == "xhs")
        check("prompt_id 已绑定 xhs 模板", art.prompt_id == pid, f"prompt_id={art.prompt_id}")
        check("title 已写", bool(art.title), str(art.title))
        usage = (art.meta or {}).get("usage", {})
        check("meta.usage 记账（mock 占位）",
              usage.get("model") == "mock" and "prompt_tokens" in usage
              and "cost_est" in usage, str(usage))
        check("meta.cover_note 已写", (art.meta or {}).get("cover_note"), str(art.meta))
        check("meta.image_plan 已写", isinstance((art.meta or {}).get("image_plan"), list)
              and len(art.meta["image_plan"]) >= 2, str(art.meta))
        check("tags 存 JSON 数组且不含 # 号",
              isinstance(art.tags, list) and 3 <= len(art.tags) <= 5
              and all("#" not in t for t in art.tags), str(art.tags))
        t = s.get(Topic, 1)
        check("topic.status → used", t.status == "used", str(t.status))
        content, tags = art.content, art.tags

    print("\n[3] M7 文案适配：正文末尾拼 #标签")
    tag_line = " ".join(f"#{x}" for x in tags)
    check("content 末尾即标签行", content.rstrip().endswith(tag_line),
          content.rstrip().rsplit("\n", 1)[-1])
    check("正文与标签行之间空一行", ("\n\n" + tag_line) in content)
    check("正文本体未被改动（含口语化开头）", content.startswith("最近试了一圈"), content[:12])

    print("\n[4] mock 产物满足 P1 量规（结构可测部分）")
    mock = XhsNote.model_validate(generator._MOCK_XHS)
    check("标题 ≤ 20 字且含 emoji", len(mock.title) <= 20 and _has_emoji(mock.title),
          f"{len(mock.title)} 字：{mock.title}")
    paras = [p for p in mock.content.split("\n\n") if p.strip()]
    max_lines = max(p.count("\n") + 1 for p in paras)
    check("正文每段 ≤ 3 行", max_lines <= 3, f"最长段 {max_lines} 行、共 {len(paras)} 段")
    n_chars = len(mock.content)
    check("正文 300-800 字", 300 <= n_chars <= 800, f"{n_chars} 字")
    check("无说教腔标记词", not any(w in mock.content for w in
          ("姐妹们记住", "一定要知道", "作为AI", "综上所述")))
    check("标签 3-5 个", 3 <= len(mock.tags) <= 5, str(mock.tags))
    check("金句 2-4 句且每句 ≤ 20 字",
          2 <= len(mock.image_quotes) <= 4 and all(len(q) <= 20 for q in mock.image_quotes),
          str(mock.image_quotes))
    check("封面文案 ≤ 12 字", len(mock.cover_text) <= 12, mock.cover_text)

    print("\n[5] 重新生成开新行 + 旧行归档（xhs）")
    resp2 = _http_generate(client, 1, platform="xhs")
    body2 = resp2.json()
    check("重新生成成功", body2.get("status") == "ready", str(body2))
    aid2 = body2["article_id"]
    check("开新行（id 不同）", aid2 != aid1, f"{aid1} -> {aid2}")
    with session_scope() as s:
        rows = s.scalars(select(Article).where(Article.topic_id == 1, Article.platform == "xhs")
                         .order_by(Article.id)).all()
        statuses = {r.id: r.status for r in rows}
        check("旧行 → archived", statuses.get(aid1) == "archived", str(statuses))
        check("新行 → ready", statuses.get(aid2) == "ready", str(statuses))

    print("\n[6] published 终态 409 + 敏感词命中 failed（xhs）")
    with session_scope() as s:
        s.add(Article(topic_id=3, platform="xhs", title="已发布", content="",
                      meta={"usage": {}}, status="published"))
    r409 = _http_generate(client, 3, platform="xhs")
    check("409 已有 published 终态行", r409.status_code == 409, str(r409.status_code))
    # 用 mock 标题里的词当敏感词，验证 xhs 词表通道
    config.SENSITIVE_FILE_XHS.write_text("回不去了\n", encoding="utf-8")
    resp3 = _http_generate(client, 2, platform="xhs")
    body3 = resp3.json()
    check("命中返回 200 + failed", resp3.status_code == 200 and body3.get("status") == "failed",
          str(body3))
    check("error 含命中词", "回不去了" in (body3.get("error") or ""), str(body3.get("error")))
    with session_scope() as s:
        art = s.get(Article, body3["article_id"])
        check("落 failed 行且 error 必填", art.status == "failed" and art.error, str(art.status))
        check("failed 行仍记 meta.usage", (art.meta or {}).get("usage") is not None, str(art.meta))
    config.SENSITIVE_FILE_XHS.write_text("", encoding="utf-8")

    print("\n[7] P0 回归（wechat）+ 400 不支持平台")
    rw = _http_generate(client, 2, platform="wechat")
    check("wechat 生成仍 ready", rw.status_code == 200 and rw.json().get("status") == "ready",
          str(rw.json()))
    with session_scope() as s:
        wart = s.get(Article, rw.json()["article_id"])
        check("wechat 行 meta.digest 仍在", (wart.meta or {}).get("digest") is not None)
        check("wechat 行 tags 仍为空", wart.tags is None, str(wart.tags))
    rbili = _http_generate(client, 1, platform="bilibili")
    check("400 不支持平台", rbili.status_code == 400, str(rbili.status_code))

    print("\n[8] 适配器单元（纯文本处理，不调 LLM）")
    note = XhsNote(
        title="测试标题🎉", content="第一段\n\n第二段",
        tags=["#AI工具 ", "AI工具", "效率", ""],
        cover_text="封面文案", image_quotes=["金句一", "金句二"],
    )
    fmt = xhs_adapter.format_note(note)
    check("标签去 # 去空去重保序", fmt.tags == ["AI工具", "效率"], str(fmt.tags))
    check("正文末尾拼去重后的标签", fmt.content.endswith("\n\n#AI工具 #效率"), fmt.content[-20:])
    check("meta.cover_note 映射 cover_text", fmt.meta["cover_note"] == "封面文案")
    check("meta.image_plan 映射 image_quotes", fmt.meta["image_plan"] == ["金句一", "金句二"])
    check("无标签时不追加空行", xhs_adapter.format_note(
        note.model_copy(update={"tags": []})).content == "第一段\n\n第二段")

    print("\n[9] JSON 解析容错（思维链模型常见输出形态，不调 LLM）")
    good = '{"title": "t", "content": "c", "tags": [], "cover_text": "", "image_quotes": []}'
    fenced = "```json\n" + good + "\n```"
    wrapped = "好的，以下是 JSON：\n" + good + "\n如上。"
    for tag, raw in [("裸 JSON", good), ("markdown 围栏", fenced), ("前后夹说明文字", wrapped)]:
        parsed = generator._parse_and_validate(raw, XhsNote)
        check(f"{tag} 可解析", parsed.title == "t", tag)
    try:
        generator._parse_and_validate("", XhsNote)
        check("空串报错并带原文开头", False)
    except ValueError as exc:
        check("空串报错并带原文开头", "不是合法 JSON" in str(exc) and "原文开头" in str(exc), str(exc)[:80])

    print("\n[9b] 小红书平台硬上限（超限在生成侧拦下，免得发布前手动剪）")
    base = {"title": "正常标题", "content": "正文", "tags": [], "cover_text": "", "image_quotes": []}
    check("20 字标题恰好通过", len(XhsNote.model_validate(
        {**base, "title": "一二三四五六七八九十一二三四五六七八九十"}).title) == 20)
    try:
        XhsNote.model_validate({**base, "title": "一二三四五六七八九十一二三四五六七八九十1"})
        check("21 字标题拒绝", False)
    except ValueError as exc:
        check("21 字标题拒绝", "20 字上限" in str(exc), str(exc)[:60])
    try:
        XhsNote.model_validate({**base, "content": "字" * 951})
        check("951 字正文拒绝（标签行余量）", False)
    except ValueError as exc:
        check("951 字正文拒绝（标签行余量）", "1000 字上限" in str(exc), str(exc)[:60])
    # 校验错误进 generator 重试链：错误文案会拼进下一轮提示让模型自纠
    try:
        generator._parse_and_validate(json.dumps({**base, "title": "超" * 21}, ensure_ascii=False), XhsNote)
        check("校验失败包装为 ValueError（可重试）", False)
    except ValueError as exc:
        check("校验失败包装为 ValueError（可重试）", "校验失败" in str(exc), str(exc)[:60])

    print("\n[10] 页面不缓存（生成后返回工作台信息实时）")
    home10 = client.get("/")
    check("HTML 带 Cache-Control: no-store",
          home10.headers.get("cache-control") == "no-store",
          str(home10.headers.get("cache-control")))
    check("页面带 bfcache 自动刷新脚本", "e.persisted" in home10.text)

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P1 全部验收项通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
