#!/usr/bin/env python
"""P3 验收脚本（可重复运行，不依赖外网与运行中的服务，无 Key 走 mock）。

覆盖任务四件套 P3 结构验收点：
  1. 选题台 GET / 200，选题按 score 倒序、radar 来源徽标
  2. 预览页 GET /articles/{id}（xhs ready）：三区文案 + 复制按钮 + 图片区与 assets 一致，
     每张图可经 /static/assets/... 200 访问
  3. GET /api/articles/{id}/package：xhs ready → 200 ZIP（title.txt / content.txt 末尾
     #标签 / images/NN_kind.png 与 assets 行数一致）；wechat / failed / 不存在 → 409/409/404
  4. 模板管理：GET /prompts 200 列出种子（P-1b 起三份）；POST 新建 version+1；PUT 启停翻转；
     generate 立即按新状态选模板（热更新，以 article.prompt_id 为证）
  5. 发布回填：POST publish → 201 + publish_records 行 + article→published；
     再 generate → 409（published 终态）
  6. 目录穿越防护：/static/assets/ 下的 .. 路径不返回文件内容
  7. 文章 API 契约：GET /api/articles 列表、GET /api/articles/{id} 详情含 assets 清单

运行：.venv/Scripts/python tests/test_p3.py
"""
import io
import sys
import tempfile
import traceback
from pathlib import Path
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + mock 模式 + 不起调度器 + assets 落临时目录；字体与版式用仓库内真实文件
_TMP = Path(tempfile.mkdtemp(prefix="p3_check_"))
config.DB_PATH = _TMP / "app.db"
config.SENSITIVE_FILE_WECHAT = _TMP / "sensitive_wechat.txt"
config.SENSITIVE_FILE_XHS = _TMP / "sensitive_xhs.txt"
config.ASSETS_DIR = _TMP / "assets"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Article, Asset, PublishRecord, Topic  # noqa: E402
from app.services import prompt_engine  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def _assets_of(aid: int) -> list[Asset]:
    with session_scope() as s:
        return list(s.scalars(select(Asset).where(Asset.article_id == aid).order_by(Asset.id)))


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    print(f"临时工作目录：{_TMP}")
    init_db()
    with session_scope() as s:
        s.add(Topic(title="DeepSeek 发布新一代大模型，编程能力大幅提升", angle="AI·编程",
                    domain="AI与编程", source="radar", status="new", score=1.2))
        s.add(Topic(title="大模型价格战再起，开发者迎来红利", angle="AI·成本",
                    domain="AI与编程", source="radar", status="new", score=1.0))
    prompt_engine.seed_prompts()
    client = TestClient(app)  # 不进 lifespan，避免起调度器；init_db 已手动完成

    # 造数据：topic1 出 xhs（页面/素材包/回填用），topic2 出 wechat（模板热更新用）+ 手工 failed 行
    aid1 = client.post("/api/topics/1/generate?platform=xhs").json()["article_id"]
    wid = client.post("/api/topics/2/generate?platform=wechat").json()["article_id"]
    with session_scope() as s:
        s.add(Article(topic_id=2, prompt_id=1, platform="xhs", title="失败稿", content="",
                      meta={"usage": {"model": "mock"}}, status="failed", error="敏感词命中：测试词"))
    with session_scope() as s:
        fid = s.scalars(select(Article.id).where(Article.status == "failed")).one()

    print("\n[1] 选题台 GET /")
    resp = client.get("/")
    check("HTTP 200 且为 HTML", resp.status_code == 200 and "text/html" in resp.headers["content-type"])
    html = resp.text
    check("列出种子选题且按 score 倒序",
          html.find("DeepSeek 发布新一代") != -1
          and html.find("DeepSeek 发布新一代") < html.find("大模型价格战再起"))
    check("radar 来源徽标 + 双端操作按钮",
          '>radar<' in html and "generate(1,'wechat',this)" in html and "generate(1,'xhs',this)" in html)
    check("灵感选题区带直触采样按钮（入队 xhs_sample）",
          "runSampleJob" in html and "/api/sampling/jobs" in html and "采样一轮" in html)

    print("\n[2] 预览页 GET /articles/{id}（xhs ready）")
    resp = client.get(f"/articles/{aid1}")
    check("HTTP 200", resp.status_code == 200)
    html = resp.text
    with session_scope() as s:
        art = s.get(Article, aid1)
    assets = _assets_of(aid1)
    check("三区文案与 articles 行一致", art.title in html and "copy-content" in html
          and "copy-tags" in html)
    check("复制按钮与素材包下载入口", ("navigator.clipboard" in html or "copyText(" in html)
          and f"/api/articles/{aid1}/package" in html)
    check("图片区张数与 assets 行数一致", html.count("/static/assets/") >= len(assets) * 2,
          f"{html.count('/static/assets/')} 处引用 / {len(assets)} 行")
    for asset in assets:
        fname = asset.path.split("/")[-1]
        r = client.get(f"/static/assets/{aid1}/{fname}")
        if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
            check(f"静态图 {fname} 可访问且为 PNG", False, str(r.status_code))
            break
    else:
        check(f"静态图全部可访问（{len(assets)} 张 PNG）", True)
    resp_f = client.get(f"/articles/{fid}")
    check("failed 行展示 error 全文与重新生成入口",
          resp_f.status_code == 200 and "敏感词命中：测试词" in resp_f.text
          and "重新生成" in resp_f.text)
    check("预览不存在 id → 404", client.get("/articles/9999").status_code == 404)
    check("xhs ready 页带违禁词体检入口（手动、计费提示）",
          "checkSensitive" in html and "sensitive-check" in html and "RedFox 计费" in html)
    resp_w = client.get(f"/articles/{wid}")
    check("wechat 页无体检入口", "checkSensitive" not in resp_w.text)

    print("\n[2b] 违禁词体检 API（RedFox 打桩，不产生真实计费调用）")
    from app.collectors import redfox as redfox_mod
    from app.collectors.redfox import RedFoxError

    real_check = redfox_mod.sensitive_word_search
    try:
        redfox_mod.sensitive_word_search = lambda text: {
            "words": ["免费领", "限时"], "categories": ["诱导行为"]}
        resp = client.post(f"/api/articles/{aid1}/sensitive-check")
        body = resp.json()
        check("xhs → 200 + 命中词/分类/计费标记",
              resp.status_code == 200 and body["words"] == ["免费领", "限时"]
              and body["categories"] == ["诱导行为"] and body["billed"] is True
              and body["checked_chars"] > 0, str(body))
        check("wechat → 409（仅小红书词库）",
              client.post(f"/api/articles/{wid}/sensitive-check").status_code == 409)
        check("不存在 id → 404",
              client.post("/api/articles/9999/sensitive-check").status_code == 404)

        def _boom(text):
            raise RedFoxError("HTTP 401：鉴权失败")
        redfox_mod.sensitive_word_search = _boom
        resp = client.post(f"/api/articles/{aid1}/sensitive-check")
        check("RedFox 失败 → 502 + 摘要透传",
              resp.status_code == 502 and "401" in resp.json()["detail"], str(resp.json()))
    finally:
        redfox_mod.sensitive_word_search = real_check

    print("\n[2c] 命中词回填本地词表（文件追加、去重、即时生效）")
    resp = client.post("/api/sensitive/xhs/words", json={"words": ["测试违禁词", " ", "测试违禁词"]})
    body = resp.json()
    check("追加成功且去重去空白", resp.status_code == 201 and body["added"] == ["测试违禁词"]
          and len(body["skipped"]) == 2, str(body))
    word_file = config.SENSITIVE_FILE_XHS.read_text(encoding="utf-8")
    check("词表文件含新词", "测试违禁词" in word_file)
    from app.services import sensitive as sensitive_mod
    check("load_words 即时可见（无需重启）",
          "测试违禁词" in sensitive_mod.load_words("xhs"))
    resp = client.post("/api/sensitive/xhs/words", json={"words": ["测试违禁词"]})
    check("重复追加 → skipped", resp.json()["added"] == [] and resp.json()["skipped"] == ["测试违禁词"])
    check("未知平台 → 422",
          client.post("/api/sensitive/bilibili/words", json={"words": ["x"]}).status_code == 422)
    check("空词表 → 422",
          client.post("/api/sensitive/xhs/words", json={"words": []}).status_code == 422)

    print("\n[3] 素材包 GET /api/articles/{id}/package")
    resp = client.get(f"/api/articles/{aid1}/package")
    check("xhs ready → 200 + application/zip",
          resp.status_code == 200 and resp.headers["content-type"] == "application/zip")
    zf = ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    images = sorted(n for n in names if n.startswith("images/"))
    check("ZIP 内含 title.txt / content.txt / images/",
          "title.txt" in names and "content.txt" in names and len(images) == len(assets),
          str(names))
    check("images 按上传顺序编号（01_cover.png、02_quote.png…）",
          images[0].endswith("01_cover.png") and "02_quote.png" in images[1], str(images))
    content_txt = zf.read("content.txt").decode("utf-8")
    check("content.txt 末尾已拼 #标签", content_txt.rstrip().splitlines()[-1].startswith("#"),
          content_txt.rstrip().splitlines()[-1])
    check("title.txt 与 articles 行一致", zf.read("title.txt").decode("utf-8") == art.title)
    check("wechat 行 → 409", client.get(f"/api/articles/{wid}/package").status_code == 409)
    check("failed 行 → 409", client.get(f"/api/articles/{fid}/package").status_code == 409)
    check("不存在 id → 404", client.get("/api/articles/9999/package").status_code == 404)

    print("\n[4] 模板管理页 + 热更新链路")
    resp = client.get("/prompts")
    check("GET /prompts 200 且列出种子模板",
          resp.status_code == 200 and "wechat" in resp.text and "xhs" in resp.text)
    seeds = client.get("/api/prompts").json()
    check("种子含 wechat+article、xhs+note 与 xhs+teardown（P-1b 加 A3；P6 另加 rewrite/title_score）",
          {(p["platform"], p["scenario"]) for p in seeds}
          == {("wechat", "article"), ("xhs", "note"), ("xhs", "teardown"),
              ("xhs", "title_score"), ("xhs", "rewrite"), ("wechat", "rewrite")},
          str([(p["platform"], p["scenario"], p["version"]) for p in seeds]))
    wechat_v1 = next(p for p in seeds if p["platform"] == "wechat")
    resp = client.post("/api/prompts", json={
        "platform": "wechat", "scenario": "article", "name": "热更新验证版",
        "template": wechat_v1["template"], "variables": wechat_v1["variables"]})
    check("POST 新建版本 → 201 + version+1", resp.status_code == 201
          and resp.json()["version"] == wechat_v1["version"] + 1, str(resp.json().get("version")))
    v2 = resp.json()
    art_v2 = client.post("/api/topics/2/generate?platform=wechat").json()
    with session_scope() as s:
        check("generate 立即选用新版本（prompt_id 为证）",
              s.get(Article, art_v2["article_id"]).prompt_id == v2["id"])
    resp = client.put(f"/api/prompts/{v2['id']}", json={"enabled": False})
    check("PUT 启停切换 → enabled 翻转", resp.status_code == 200
          and resp.json()["enabled"] is False)
    art_back = client.post("/api/topics/2/generate?platform=wechat").json()
    with session_scope() as s:
        check("停用后 generate 回落到 v1（不重启即生效）",
              s.get(Article, art_back["article_id"]).prompt_id == wechat_v1["id"])
    check("PUT 不存在 id → 404", client.put("/api/prompts/9999", json={"enabled": True}).status_code == 404)

    print("\n[5] 发布回填 POST /api/articles/{id}/publish")
    resp = client.post(f"/api/articles/{aid1}/publish", json={
        "platform": "xhs", "account": "主号",
        "url": "https://www.xiaohongshu.com/explore/test",
        "metrics": {"likes": 320, "collects": 88, "comments": 21}})
    check("201 + publish_records 行", resp.status_code == 201
          and resp.json()["platform"] == "xhs" and resp.json()["metrics"]["likes"] == 320,
          str(resp.status_code))
    with session_scope() as s:
        rec = s.scalars(select(PublishRecord).where(PublishRecord.article_id == aid1)).one()
        art = s.get(Article, aid1)
        check("article → published（终态）", art.status == "published", art.status)
        check("回填字段完整落库", rec.account == "主号" and rec.url.endswith("/test"))
    resp = client.post("/api/topics/1/generate?platform=xhs")
    check("published 后再 generate → 409", resp.status_code == 409, str(resp.status_code))
    check("不存在 id 回填 → 404",
          client.post("/api/articles/9999/publish", json={"platform": "xhs"}).status_code == 404)

    print("\n[6] 目录穿越防护")
    for evil in ("/static/assets/1/..%2Fapp.db", "/static/assets/1/..%2F..%2Fapp.db",
                 "/static/assets/0/x.png"):
        r = client.get(evil)
        if r.status_code in (200,):
            check(f"穿越路径 {evil} 被拒绝", False, str(r.status_code))
            break
    else:
        check("穿越/非法路径均不返回文件内容（404/405/422）", True)

    print("\n[7] 文章 API 契约")
    listing = client.get("/api/articles").json()
    check("GET /api/articles 返回列表", isinstance(listing, list) and len(listing) >= 3)
    detail = client.get(f"/api/articles/{aid1}").json()
    check("详情含 assets 清单且与表一致",
          len(detail.get("assets", [])) == len(assets)
          and detail["assets"][0]["kind"] == "cover")
    check("详情不存在 id → 404", client.get("/api/articles/9999").status_code == 404)

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P3 全部验收项通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
