#!/usr/bin/env python
"""P0 验收脚本（可重复运行，不依赖外网与运行中的服务，无 Key 走 mock）。

覆盖计划书 P0 / SDD 4.2 验收点：
  1. 种子模板入库（幂等键 wechat+article+v1，重启不覆盖）
  2. 端到端 mock 生成：POST generate → articles 写 ready 行 + meta.usage 记账 + topic→used
  3. 模板热更新：改库不重启，prompt_engine 现读库即用新文案（不进程缓存模板）
  4. 重新生成开新行 + 旧行归档（计划书 5.2 状态流转）
  5. 敏感词命中 → status=failed + error 注明命中词
  6. 404 topic 不存在；409 已有 published 终态行；400 不支持平台(xhs 留 P1)
  7. 真实 LLM 路径逻辑（patch _call_llm，不发 HTTP）：成功 / 重试成功 / 3 轮失败

运行：.venv/bin/python tests/test_p0.py
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + mock 模式 + 不起调度器，确保离线可重复
_TMP = Path(tempfile.mkdtemp(prefix="p0_check_"))
config.DB_PATH = _TMP / "app.db"
config.SENSITIVE_FILE_WECHAT = _TMP / "sensitive_wechat.txt"
config.SENSITIVE_FILE_XHS = _TMP / "sensitive_xhs.txt"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""

from sqlalchemy import select, text  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Article, Prompt, Topic  # noqa: E402
from app.schemas import WechatArticle  # noqa: E402
from app.services import generator, prompt_engine  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def _insert_topics() -> None:
    with session_scope() as s:
        s.add(Topic(title="DeepSeek 发布新一代大模型，编程能力大幅提升", angle="AI·编程",
                    domain="AI与编程", source="radar", status="new", score=1.2,
                    evidence={"items": [{"title": "DeepSeek 新模型", "url": "https://x.com/hot/1"}]}))
        s.add(Topic(title="大模型价格战再起，开发者迎来红利", angle="AI·成本",
                    domain="AI与编程", source="radar", status="new", score=1.0))
        s.add(Topic(title="写公众号还有前途吗", angle="自媒体·趋势",
                    domain="自媒体", source="radar", status="new", score=0.8))


def _http_generate(client, topic_id: int, platform: str = "wechat", prompt_id: int | None = None):
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

    print("\n[1] 种子模板入库（幂等键 wechat+article+v1）")
    seeded = prompt_engine.seed_prompts()
    # P1 起 SEED_FILES 还含 xhs_note.yml，此处只断言公众号键已入库且 xhs 键随之入库
    check("公众号模板首次入库", "wechat+article+v1" in seeded, str(seeded))
    reseeded = prompt_engine.seed_prompts()
    check("重复入库跳过（重启不覆盖）", reseeded == [], str(reseeded))
    with session_scope() as s:
        p = s.scalars(select(Prompt).where(Prompt.platform == "wechat")).first()
        check("模板字段完整", p is not None and p.platform == "wechat"
              and p.scenario == "article" and p.version == 1 and p.enabled, str(p and p.platform))
        check("模板含 system/user 标记", p is not None and "# system" in p.template
              and "# user" in p.template)

    print("\n[2] 端到端 mock 生成（无 Key 走 mock）")
    client = TestClient(app)  # 不进 lifespan，避免起调度器；init_db 已手动完成
    resp = _http_generate(client, 1)
    body = resp.json()
    check("HTTP 200", resp.status_code == 200, str(resp.status_code))
    check("返回 article_id 与 status=ready", body.get("status") == "ready"
          and isinstance(body.get("article_id"), int), str(body))
    aid1 = body["article_id"]
    with session_scope() as s:
        art = s.get(Article, aid1)
        check("articles 写 ready 行", art is not None and art.status == "ready", str(art and art.status))
        check("platform=wechat", art.platform == "wechat")
        check("prompt_id 已绑定", art.prompt_id == 1, f"prompt_id={art.prompt_id}")
        check("content 存 Markdown", art.content and len(art.content) > 0)
        usage = (art.meta or {}).get("usage", {})
        check("meta.usage 记账（mock 占位）",
              usage.get("model") == "mock" and "prompt_tokens" in usage
              and "cost_est" in usage, str(usage))
        check("meta.digest 已写", (art.meta or {}).get("digest"), str(art.meta))
        check("无 html / draft_media_id（M6 才填）",
              "html" not in (art.meta or {}) and "draft_media_id" not in (art.meta or {}))
        t = s.get(Topic, 1)
        check("topic.status → used", t.status == "used", str(t.status))

    print("\n[3] 模板热更新（改库不重启，prompt_engine 现读库不缓存）")
    with session_scope() as s:
        s.execute(text("UPDATE prompts SET template=REPLACE(template,'信息密度高','改过的文案') "
                       "WHERE id=1"))
    with session_scope() as s:
        _, sm, um = prompt_engine.render_messages(s, "wechat", "article",
                                                   {"title": "T", "angle": "A", "domain": "D",
                                                    "reference_points": ""})
    check("改后文案出现在渲染结果", "改过的文案" in um, um[:60])
    check("原文案已消失", "信息密度高" not in um, um[:60])

    print("\n[4] 重新生成开新行 + 旧行归档（计划书 5.2）")
    resp2 = _http_generate(client, 1)
    body2 = resp2.json()
    check("重新生成成功", body2.get("status") == "ready", str(body2))
    aid2 = body2["article_id"]
    check("开新行（id 不同）", aid2 != aid1, f"{aid1} -> {aid2}")
    with session_scope() as s:
        rows = s.scalars(select(Article).where(Article.topic_id == 1, Article.platform == "wechat")
                         .order_by(Article.id)).all()
        statuses = {r.id: r.status for r in rows}
        check("旧行 → archived", statuses.get(aid1) == "archived", str(statuses))
        check("新行 → ready", statuses.get(aid2) == "ready", str(statuses))

    print("\n[5] 敏感词命中 → status=failed + error 注明命中词")
    config.SENSITIVE_FILE_WECHAT.write_text("信息密度高\n", encoding="utf-8")
    resp3 = _http_generate(client, 2)
    body3 = resp3.json()
    check("命中返回 200 + failed", resp3.status_code == 200 and body3.get("status") == "failed",
          str(body3))
    check("error 含命中词", "信息密度高" in (body3.get("error") or ""), str(body3.get("error")))
    aid3 = body3["article_id"]
    with session_scope() as s:
        art = s.get(Article, aid3)
        check("落 failed 行且 error 必填", art.status == "failed" and art.error, str(art.status))
        check("failed 行仍记 meta.usage", (art.meta or {}).get("usage") is not None, str(art.meta))
    # 清空词表，恢复后续检查
    config.SENSITIVE_FILE_WECHAT.write_text("", encoding="utf-8")

    print("\n[6] 404 / 409 / 400")
    r404 = _http_generate(client, 9999)
    check("404 topic 不存在", r404.status_code == 404, str(r404.status_code))
    # 预置一条 published 终态行，触发生成应 409
    with session_scope() as s:
        s.add(Article(topic_id=3, platform="wechat", title="已发布", content="",
                      meta={"usage": {}}, status="published"))
    r409 = _http_generate(client, 3)
    check("409 已有 published 终态行", r409.status_code == 409, str(r409.status_code))
    rxhs = _http_generate(client, 1, platform="xhs")
    check("xhs 已支持（P1 注册，返回 ready）",
          rxhs.status_code == 200 and rxhs.json().get("status") == "ready", str(rxhs.json()))
    rdouyin = _http_generate(client, 1, platform="douyin")
    check("400 不支持平台", rdouyin.status_code == 400, str(rdouyin.status_code))

    print("\n[7] 真实 LLM 路径逻辑（patch _call_llm，不发 HTTP）")
    _run_realpath_tests()

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P0 全部验收项通过")
    return 0


def _run_realpath_tests() -> None:
    """不开 HTTP，直接调 generator.generate 并 patch _call_llm，验证真实路径分支。"""
    saved_mock = config.LLM_MOCK
    saved_key = config.OPENAI_API_KEY
    config.LLM_MOCK = False
    config.OPENAI_API_KEY = "fake-key-for-test"

    valid = json.dumps(generator._MOCK_WECHAT, ensure_ascii=False)
    u1 = {"prompt_tokens": 100, "completion_tokens": 200}

    try:
        # 7a 一次成功
        with patch("app.services.generator._call_llm", return_value=(valid, u1)) as m:
            r = generator.generate("wechat", WechatArticle, "sys", "usr")
        check("真实路径一次成功", r.ok and r.article is not None and m.call_count == 1, str(r.error))
        check("usage 记 token 与成本", r.usage["prompt_tokens"] == 100
              and r.usage["completion_tokens"] == 200 and r.usage["model"] == config.MODEL_NAME
              and r.usage["cost_est"] > 0, str(r.usage))

        # 7b 前两轮校验失败、第三轮成功（重试追加错误信息）
        bad = '{"title": "x"}'  # 缺 digest / content_md，校验失败
        calls = {"i": 0}

        # _real_generate 会传第三参 llm（一次生成内固定同一份生效配置）
        def side(system, user, llm=None):
            calls["i"] += 1
            if calls["i"] < 3:
                return bad, {"prompt_tokens": 10, "completion_tokens": 5}
            return valid, {"prompt_tokens": 50, "completion_tokens": 60}

        with patch("app.services.generator._call_llm", side_effect=side) as m:
            r = generator.generate("wechat", WechatArticle, "sys", "usr")
        check("重试两次后第三轮成功", r.ok and m.call_count == 3, f"calls={m.call_count}")
        check("重试 token 累计（10+5+10+5+50+60）",
              r.usage["prompt_tokens"] == 70 and r.usage["completion_tokens"] == 70, str(r.usage))
        # 验证重试时把错误信息追加进 user 消息
        last_call_user = m.call_args_list[-1].args[1]
        check("重试提示追加错误信息", "上一次输出存在问题" in last_call_user, last_call_user[-40:])

        # 7c 三轮全失败 → failed
        with patch("app.services.generator._call_llm",
                   side_effect=Exception("boom")) as m:
            r = generator.generate("wechat", WechatArticle, "sys", "usr")
        check("3 轮失败后落 failed", not r.ok and r.article is None and r.error, str(r.error))
        check("失败仍记 usage", r.usage is not None and r.usage["model"] == config.MODEL_NAME,
              str(r.usage))
        check("重试封顶 3 次（首发+2）", m.call_count == 3, f"calls={m.call_count}")
    finally:
        config.LLM_MOCK = saved_mock
        config.OPENAI_API_KEY = saved_key


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
