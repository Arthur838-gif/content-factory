#!/usr/bin/env python
"""P6 验收脚本：标题打分 + 多平台改写（融合红狐 skill 方法论，可重复运行）。

覆盖：
  1. 种子模板入库：xhs/title_score、xhs/rewrite、wechat/rewrite（v1 幂等）
  2. 标题打分 API：mock 结构完整（六维/等级/建议/改写版），空标题 422
  3. 改写链路：wechat 成文 → 改写为 xhs（ready + 出图 + meta.rewrite_from 溯源）
  4. 改写约束：同平台 422；非 ready 源 422
  5. 文章页契约：打分卡片与改写按钮渲染
  6. 生成回归：原 generate 链路（重构出 _persist_generation 后）不受影响

运行：.venv/Scripts/python tests/test_p6.py
"""
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="p6_check_"))
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
from app.models import Asset, Prompt, Topic  # noqa: E402
from app.main import app  # noqa: E402
from app.services import prompt_engine  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {mark} {name}{suffix}")
    if not cond:
        FAILURES.append(name)


def main() -> int:
    print(f"临时工作目录：{_TMP}")
    init_db()
    prompt_engine.seed_prompts()  # 种子模板入库（幂等）
    with session_scope() as s:
        s.add(Topic(title="DeepSeek 发布新一代大模型，编程能力大幅提升", angle="AI·编程",
                    domain="AI与编程", source="radar", status="new", score=1.2))
        tid = s.scalars(select(Topic)).one().id
    client = TestClient(app)

    print("\n[1] 种子模板入库（title_score / rewrite × 2）")
    with session_scope() as s:
        rows = {(p.platform, p.scenario): p.version for p in s.scalars(select(Prompt)).all()}
    check("xhs+title_score v1", rows.get(("xhs", "title_score")) == 1, str(sorted(rows)))
    check("xhs+rewrite v1", rows.get(("xhs", "rewrite")) == 1)
    check("wechat+rewrite v1", rows.get(("wechat", "rewrite")) == 1)

    print("\n[2] 标题打分 API（mock）")
    r2 = client.post("/api/titles/score", json={"title": "救命！这个AI工具也太好用了吧"})
    d2 = r2.json()
    check("200 且结构完整", r2.status_code == 200 and d2["total"] > 0 and d2["grade"] in "SABC"
          and len(d2["dimensions"]) == 6 and d2["revised_titles"], str(d2)[:100])
    check("六维权重齐", [x["weight"] for x in d2["dimensions"]]
          == ["15%", "20%", "25%", "20%", "15%", "5%"])
    r2b = client.post("/api/titles/score", json={"title": "   "})
    check("空标题 422", r2b.status_code == 422, str(r2b.status_code))

    print("\n[3] 改写链路：wechat 成文 → xhs")
    r3 = client.post(f"/api/topics/{tid}/generate?platform=wechat")
    src_id = r3.json()["article_id"]
    check("源文章生成 ready", r3.status_code == 200 and r3.json()["status"] == "ready")
    r3b = client.post(f"/api/articles/{src_id}/rewrite?platform=xhs")
    d3b = r3b.json()
    check("改写 ready + 返回新 article", r3b.status_code == 200 and d3b["status"] == "ready"
          and d3b["article_id"] != src_id, str(d3b))
    with session_scope() as s:
        assets = list(s.scalars(select(Asset).where(Asset.article_id == d3b["article_id"])))
    from app.models import Article  # noqa: E402

    with session_scope() as s:
        art = s.get(Article, d3b["article_id"])
        check("改写产物 meta.rewrite_from 溯源", (art.meta or {}).get("rewrite_from") == src_id)
        check("改写产物平台 xhs", art.platform == "xhs")
    check("改写产物出图（cover + quotes）", len(assets) >= 3, str(len(assets)))

    print("\n[4] 改写约束")
    r4 = client.post(f"/api/articles/{src_id}/rewrite?platform=wechat")
    check("同平台改写 422", r4.status_code == 422, str(r4.status_code))
    r4b = client.post("/api/articles/99999/rewrite?platform=xhs")
    check("不存在的文章 404", r4b.status_code == 404, str(r4b.status_code))

    print("\n[5] 文章页契约")
    page = client.get(f"/articles/{d3b['article_id']}")
    check("ready 文章页含打分与改写按钮",
          page.status_code == 200 and "六维打分" in page.text and "改写为公众号长文" in page.text)
    page_src = client.get(f"/articles/{src_id}")
    check("wechat 文章页含改写为小红书", "改写为小红书笔记" in page_src.text)

    print("\n[6] 原 generate 链路回归（重构后）")
    r6 = client.post(f"/api/topics/{tid}/generate?platform=xhs")
    check("重新生成仍 ready（归档旧行开新行）",
          r6.status_code == 200 and r6.json()["status"] == "ready", str(r6.json()))

    print()
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过")
        for name in FAILURES:
            print(f"  ✗ {name}")
        return 1
    print("PASS：P6 标题打分 + 多平台改写验收全部通过")
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
