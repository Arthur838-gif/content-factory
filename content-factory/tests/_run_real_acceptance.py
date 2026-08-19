"""真实质量验收（调真实 LLM，计费）：生成文章 → 结构 rubric → 预览/素材包。

用法：.venv/Scripts/python tests/_run_real_acceptance.py <输出目录>
隔离：数据库/素材/备份全部落 <输出目录>，绝不写正式 data/app.db 与 data/assets
（在导入 app 前用环境变量重定向，config 读 env 优先于 .env）。
退出码：自动项（生成状态 / rubric / 预览 / 素材包）任一不通过 → 1。
"""
import json
import os
import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

if len(sys.argv) != 2:
    print("用法：python tests/_run_real_acceptance.py <输出目录>", file=sys.stderr)
    raise SystemExit(2)
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
# 隔离必须在导入 app 之前：config 在 import 时读环境变量。
# 只重定向有状态的写入（库/素材/备份）；版式与字体是只读输入，仍用仓库内 data/。
os.environ["CF_DB_PATH"] = str(out / "app.db")
os.environ["CF_ASSETS_DIR"] = str(out / "assets")
os.environ["CF_BACKUP_DIR"] = str(out / "backups")
os.environ["RUN_SCHEDULER"] = "0"
os.environ["CF_WORKER_EMBEDDED"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Article, Asset, Topic  # noqa: E402
from app.services import prompt_engine  # noqa: E402

report = {"run_at": datetime.now().isoformat(timespec="seconds"), "environment": {"model": config.MODEL_NAME, "base_url": config.OPENAI_BASE_URL, "mock": config.LLM_MOCK, "db": str(config.DB_PATH), "assets": str(config.ASSETS_DIR)}, "results": {}, "manual_review": []}


def has_emoji(text: str) -> bool:
    return bool(re.search(
        "["
        "\U0001F1E6-\U0001F1FF"
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FAFF"
        "\u2600-\u26FF"
        "\u2700-\u27BF"
        "]",
        text,
    ))


def asset_file_exists(article_id: int, asset: Asset) -> bool:
    return (config.ASSETS_DIR / str(article_id) / Path(asset.path).name).is_file()


def save():
    (out / "acceptance-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

init_db()
prompt_engine.seed_prompts()
with session_scope() as session:
    topics = [
        Topic(title="AI 编程助手如何帮助独立开发者提高交付效率", angle="效率实践", domain="AI与编程", source="manual", status="new", score=9.0),
        Topic(title="普通人如何用 AI 整理会议纪要并推进协作", angle="职场效率", domain="职场", source="manual", status="new", score=8.0),
        Topic(title="小团队选择大模型时应该比较哪些成本与能力", angle="工具决策", domain="AI与编程", source="manual", status="new", score=7.0),
        Topic(title="企业部署生成式 AI 前需要做好哪些数据安全准备", angle="安全治理", domain="科技", source="manual", status="new", score=6.0),
    ]
    session.add_all(topics)
    session.flush()
    ids = [t.id for t in topics]

client = TestClient(app)
wechat_response = client.post(f"/api/topics/{ids[3]}/generate?platform=wechat")
xhs_runs = []
for topic_id in ids[:3]:
    response = client.post(f"/api/topics/{topic_id}/generate?platform=xhs")
    xhs_runs.append({"topic_id": topic_id, "http_status": response.status_code, "body": response.json()})
report["results"]["p0_real_wechat"] = {"http_status": wechat_response.status_code, "body": wechat_response.json()}
report["results"]["p1_p2_real_xhs"] = xhs_runs

with session_scope() as session:
    wechat_id = wechat_response.json().get("article_id")
    wechat = session.get(Article, wechat_id) if wechat_id else None
    report["results"]["p0_real_wechat"]["article"] = {"id": wechat.id if wechat else None, "status": wechat.status if wechat else None, "usage": (wechat.meta or {}).get("usage") if wechat else None, "error": wechat.error if wechat else None}
    for item in xhs_runs:
        aid = item["body"].get("article_id")
        article = session.get(Article, aid) if aid else None
        if article is None:
            item["article"] = None
            continue
        assets = list(session.scalars(select(Asset).where(Asset.article_id == aid).order_by(Asset.id)))
        title = article.title or ""
        tags = article.tags or []
        content = article.content or ""
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        rubric = {"title_at_most_20_chars": len(title) <= 20, "title_has_emoji": has_emoji(title), "tags_count_3_to_5": 3 <= len(tags) <= 5, "tags_appended_to_content": bool(tags) and content.rstrip().endswith(" ".join("#" + str(t).lstrip("#") for t in tags)), "paragraphs_at_most_3_lines": all(len(p.splitlines()) <= 3 for p in paragraphs), "assets_cover_plus_2_quotes": len(assets) >= 3 and assets[0].kind == "cover" and all(a.kind == "quote" for a in assets[1:]), "assets_1080x1440": bool(assets) and all(a.width == 1080 and a.height == 1440 for a in assets), "asset_files_exist": bool(assets) and all(asset_file_exists(aid, a) for a in assets)}
        item["article"] = {"id": article.id, "status": article.status, "title": article.title, "content": article.content, "tags": tags, "meta": article.meta, "error": article.error, "assets": [{"kind": a.kind, "path": a.path, "width": a.width, "height": a.height} for a in assets], "rubric": rubric, "rubric_pass_count": sum(rubric.values())}

ready = next((x for x in xhs_runs if x.get("article", {}).get("status") == "ready"), None)
p3 = {"candidate_article_id": ready["article"]["id"] if ready else None}
if ready:
    aid = ready["article"]["id"]
    page = client.get(f"/articles/{aid}")
    package = client.get(f"/api/articles/{aid}/package")
    static_checks = []
    for asset in ready["article"]["assets"]:
        filename = Path(asset["path"]).name
        response = client.get(f"/static/assets/{aid}/{filename}")
        static_checks.append({"filename": filename, "status": response.status_code, "content_type": response.headers.get("content-type")})
    zip_names = []
    zip_content = {}
    if package.status_code == 200:
        with ZipFile(BytesIO(package.content)) as archive:
            zip_names = archive.namelist()
            zip_content = {n: archive.read(n).decode("utf-8") for n in ("title.txt", "content.txt") if n in archive.namelist()}
    p3.update({"preview_status": page.status_code, "preview_has_title": ready["article"]["title"] in page.text, "preview_has_copy_buttons": page.text.count("复制") >= 3, "package_status": package.status_code, "package_content_type": package.headers.get("content-type"), "zip_names": zip_names, "zip_text": zip_content, "static_assets": static_checks})
report["results"]["p3_real_preview_and_package"] = p3
report["manual_review"] = ["P1：人工检查三篇真实小红书文案的说服力、实操价值与内容质量。", "P2：人工检查图片中文显示、层级、边距、换行、截断与溢出。", "P3：浏览器打开真实预览页，复制并保存素材，在小红书 App 手动发布并计时，目标不超过 2 分钟。", "成本：usage.cost_est 仍按默认 DeepSeek 单价估算，未配置 GLM 官方价格。"]

# 自动项判定：生成状态 / 结构 rubric / 预览与素材包，任一不通过 → 非零退出
failures: list[str] = []
wechat_report = report["results"]["p0_real_wechat"]
if wechat_report.get("http_status") != 200 or wechat_report.get("article", {}).get("status") != "ready":
    failures.append(f"P0 公众号文章未就绪（HTTP {wechat_report.get('http_status')}，状态 {wechat_report.get('article', {}).get('status')}）")
for i, item in enumerate(xhs_runs, 1):
    article = item.get("article") or {}
    rubric = article.get("rubric") or {}
    if item.get("http_status") != 200 or article.get("status") != "ready":
        failures.append(f"P1/P2 小红书样本 {i} 未就绪（HTTP {item.get('http_status')}，状态 {article.get('status')}）")
    elif article.get("rubric_pass_count") != len(rubric):
        failed_items = [k for k, v in rubric.items() if not v]
        failures.append(f"P1/P2 小红书样本 {i} 结构项 {article.get('rubric_pass_count')}/{len(rubric)}（未过：{'、'.join(failed_items)}）")
if p3.get("preview_status") != 200 or p3.get("package_status") != 200:
    failures.append(f"P3 预览/素材包不可用（HTTP {p3.get('preview_status')} / {p3.get('package_status')}）")
report["failures"] = failures

save()
lines = ["# GLM-4.7 真实质量验收结果", "", f"- 执行时间：{report['run_at']}", f"- 模型：{config.MODEL_NAME}", "- 运行隔离：临时 SQLite 与素材目录，未修改正式 data/app.db。", "", "## 自动结果", "", f"- P0 WeChat：HTTP {wechat_response.status_code}，状态 `{report['results']['p0_real_wechat']['article']['status']}`。"]
usage = report["results"]["p0_real_wechat"]["article"].get("usage") or {}
lines.append(f"- WeChat usage：模型 `{usage.get('model')}`，prompt_tokens={usage.get('prompt_tokens')}，completion_tokens={usage.get('completion_tokens')}。")
for i, item in enumerate(xhs_runs, 1):
    a = item.get("article")
    lines.append(f"- P1/P2 小红书样本 {i}：" + (f"文章 #{a['id']}，HTTP {item['http_status']}，状态 `{a['status']}`，自动结构项 {a['rubric_pass_count']}/{len(a['rubric'])} 通过。" if a else f"HTTP {item['http_status']}，未取得文章记录。"))
lines += [f"- P3 真实预览：页面 HTTP {p3.get('preview_status')}，素材包 HTTP {p3.get('package_status')}，ZIP 文件数 {len(p3.get('zip_names', []))}。", "", "## 产物位置", "", "- 机器可读结果：`acceptance-result.json`", "- 小红书图片：`assets/<文章ID>/`", "- 隔离数据库：`app.db`", "", "## 尚需人工验收", ""] + [f"- {x}" for x in report["manual_review"]]
(out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"report": str(out / "README.md"), "result": str(out / "acceptance-result.json"), "p0_status": report["results"]["p0_real_wechat"]["article"]["status"], "xhs": [{"id": x.get("article", {}).get("id"), "status": x.get("article", {}).get("status"), "score": x.get("article", {}).get("rubric_pass_count")} for x in xhs_runs], "p3": p3, "failures": failures}, ensure_ascii=False, default=str))
if failures:
    print("FAIL：真实验收自动项未通过")
    for line in failures:
        print(f"  ✗ {line}")
    raise SystemExit(1)
print("PASS：真实验收自动项全部通过（人工项见 README）")
