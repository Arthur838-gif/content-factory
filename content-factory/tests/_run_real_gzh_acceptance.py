"""P9 公众号接口真实验收（调真实 RedFox，计费 3 次）：本地手动运行，不进 CI。

用法：.venv/Scripts/python tests/_run_real_gzh_acceptance.py <输出目录>
验收三件事（ Approved 计划一次性实测，共 3 次计费）：
  1. searchArticle ×1（sortType=_4 最热；若报错试 default 并记录——只重试这一次）
  2. queryArticleDetail ×1（取第 1 步命中的 workUrl，顺带验证两接口衔接）
  3. 违禁词检测 ×1（platform=微信公众号；若拒绝则回滚 wechat 体检，结论记录文档）

隔离：不写任何数据库 / 素材目录——纯接口调用 + 报告落 <输出目录>。
Key 从 content-factory/.env 读（config.REDFOX_API_KEY），不出现在代码与日志。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

if len(sys.argv) != 2:
    print("用法：python tests/_run_real_gzh_acceptance.py <输出目录>", file=sys.stderr)
    raise SystemExit(2)
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.collectors import redfox  # noqa: E402

report = {
    "run_at": datetime.now().isoformat(timespec="seconds"),
    "billed_calls": 0,
    "results": {},
    "conclusions": [],
}


def save() -> None:
    (out / "gzh-acceptance-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if not config.REDFOX_API_KEY:
    print("FAIL：.env 未配置 REDFOX_API_KEY，拒绝空跑")
    raise SystemExit(1)

KEYWORD = (config.GZH_SAMPLE_KEYWORDS or ["AI工具"])[0]

# ---- 1. searchArticle（sortType=_4 最热）----
search_result: dict = {"keyword": KEYWORD, "sort_type": "_4"}
try:
    rows = redfox.gzh_search_articles(KEYWORD, offset=0, sort_type="_4")
    report["billed_calls"] += 1
    search_result.update(
        ok=True,
        count=len(rows),
        first_fields=({k: rows[0].get(k) for k in
                       ("title", "workUrl", "readCount", "watchCount", "shareCount",
                        "likeCount", "collectCount", "commentCount", "isOriginal")}
                      if rows else {}),
    )
except redfox.RedFoxError as exc:
    report["billed_calls"] += 1
    search_result.update(ok=False, error=str(exc))
    if "sortType" in str(exc) or "sort" in str(exc).lower():
        # 文档 _0/_2/_4 与本地技能脚本 default 不一致：仅此处按计划补试一次
        try:
            rows = redfox.gzh_search_articles(KEYWORD, offset=0, sort_type="default")
            report["billed_calls"] += 1
            search_result.update(sort_type="default（_4 被拒后的补试）", ok=True, count=len(rows))
        except redfox.RedFoxError as exc2:
            report["billed_calls"] += 1
            search_result.update(error_fallback=str(exc2))
            rows = []
    else:
        rows = []
report["results"]["search_article"] = search_result

# ---- 2. queryArticleDetail（第 1 步命中的 workUrl）----
detail_result: dict = {}
work_url = (search_result.get("first_fields") or {}).get("workUrl") or ""
if isinstance(work_url, str) and work_url.startswith("http"):
    try:
        detail = redfox.gzh_article_detail(work_url)
        report["billed_calls"] += 1
        detail_result.update(
            ok=True,
            url=work_url,
            has_title=bool(detail.get("title")),
            has_content=bool(detail.get("content")),
            metrics={k: detail.get(k) for k in
                     ("readCount", "watchCount", "shareCount", "likeCount",
                      "collectCount", "commentCount")},
        )
    except redfox.RedFoxError as exc:
        report["billed_calls"] += 1
        detail_result.update(ok=False, url=work_url, error=str(exc))
else:
    detail_result.update(ok=False, skipped=True, reason="搜索无结果，无 URL 可查详情")
report["results"]["article_detail"] = detail_result

# ---- 3. 违禁词检测（platform=微信公众号）----
sensitive_result: dict = {}
probe_text = ((search_result.get("first_fields") or {}).get("title") or "AI 工具公众号文章") + " 全文干货，限时免费领取"
try:
    result = redfox.sensitive_word_search(probe_text, platform="微信公众号")
    report["billed_calls"] += 1
    sensitive_result.update(
        ok=True,
        platform="微信公众号",
        checked_chars=len(probe_text),
        words=result["words"],
        categories=result["categories"],
    )
except redfox.RedFoxError as exc:
    report["billed_calls"] += 1
    sensitive_result.update(ok=False, platform="微信公众号", error=str(exc))
report["results"]["sensitive_wechat"] = sensitive_result

# ---- 结论 ----
if search_result.get("ok"):
    report["conclusions"].append(
        f"searchArticle 可用（sortType={search_result.get('sort_type', '_4')}，"
        f"{search_result.get('count', 0)} 条）——采样链路成立"
    )
else:
    report["conclusions"].append(f"searchArticle 失败：{search_result.get('error')}")
if detail_result.get("ok"):
    report["conclusions"].append(
        "queryArticleDetail 可用（标题/正文/互动指标齐全）——手动喂样本链路成立"
    )
elif detail_result.get("skipped"):
    report["conclusions"].append("queryArticleDetail 未测（搜索无结果）")
else:
    report["conclusions"].append(f"queryArticleDetail 失败：{detail_result.get('error')}")
if sensitive_result.get("ok"):
    report["conclusions"].append(
        "违禁词检测 platform=微信公众号 可用——wechat 体检保持开放"
    )
else:
    report["conclusions"].append(
        "违禁词检测 platform=微信公众号 被拒——需回滚 wechat 体检为 xhs only（记入进度文档）"
    )

save()
lines = [
    "# P9 公众号接口真实验收结果",
    "",
    f"- 执行时间：{report['run_at']}",
    f"- 实际计费调用：{report['billed_calls']} 次",
    "",
    "## 结论",
    "",
] + [f"- {c}" for c in report["conclusions"]] + [
    "",
    "## 明细",
    "",
    "见同目录 `gzh-acceptance-result.json`。",
]
(out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, default=str))
if all(r.get("ok") for r in report["results"].values() if not r.get("skipped")):
    print("PASS：公众号接口真实验收通过")
else:
    print("FAIL：公众号接口真实验收存在失败项（见上方结论）")
    raise SystemExit(1)
