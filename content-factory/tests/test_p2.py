#!/usr/bin/env python
"""P2 验收脚本（可重复运行，不依赖外网与运行中的服务，无 Key 走 mock）。

覆盖任务四件套 P2 结构验收点：
  1. 端到端 mock 生成 platform=xhs：自动出图 1 封面 + ≥2 金句图，assets 表登记一致
  2. 中文无乱码无截断：emoji 剥离、超长自动缩字号而非截断、渲染像素非空
  3. 重复生成不残留：开新行后新目录文件数与 assets 行数一致，旧目录保留
  4. wechat_cover 版式独立渲染 900×383（调用方 M6 留后续）
  5. checklist_card 版式独立渲染（不接生成链路）
  6. 字体缺失：generate xhs 落 failed，error 提示字体放置方法
  7. P0 回归：wechat 生成不受影响（且不为 wechat 出图）；400 不支持平台
  8. imaging / render_assets 单元：编号、清旧幂等、sanitize

运行：.venv/Scripts/python tests/test_p2.py
"""
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

# 临时库 + mock 模式 + 不起调度器 + assets 落临时目录；字体与版式用仓库内真实文件
_TMP = Path(tempfile.mkdtemp(prefix="p2_check_"))
config.DB_PATH = _TMP / "app.db"
config.SENSITIVE_FILE_WECHAT = _TMP / "sensitive_wechat.txt"
config.SENSITIVE_FILE_XHS = _TMP / "sensitive_xhs.txt"
config.ASSETS_DIR = _TMP / "assets"
config.LLM_MOCK = True
config.RUN_SCHEDULER = False
config.NOTIFY_WEBHOOK = ""

from PIL import Image  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.adapters import xhs as xhs_adapter  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Article, Asset, Topic  # noqa: E402
from app.services import imagegen, imaging, prompt_engine  # noqa: E402
from PIL import Image  # noqa: E402

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
                    domain="AI与编程", source="radar", status="new", score=1.2))
        s.add(Topic(title="大模型价格战再起，开发者迎来红利", angle="AI·成本",
                    domain="AI与编程", source="radar", status="new", score=1.0))


def _gen(client, topic_id: int, platform: str):
    return client.post(f"/api/topics/{topic_id}/generate?platform={platform}")


def _assets_of(aid: int) -> list[Asset]:
    with session_scope() as s:
        return list(s.scalars(select(Asset).where(Asset.article_id == aid).order_by(Asset.id)))


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    print(f"临时工作目录：{_TMP}")
    init_db()
    _insert_topics()
    prompt_engine.seed_prompts()  # 种子模板入库（幂等）
    client = TestClient(app)  # 不进 lifespan，避免起调度器；init_db 已手动完成

    print("\n[1] 端到端 mock 生成（platform=xhs）→ 自动出图 + assets 登记")
    resp = _gen(client, 1, "xhs")
    body = resp.json()
    check("HTTP 200 + ready", resp.status_code == 200 and body.get("status") == "ready", str(body))
    aid1 = body["article_id"]
    art_dir = config.ASSETS_DIR / str(aid1)
    files = sorted(p.name for p in art_dir.iterdir())
    check("生成 1 封面 + ≥2 金句图（编号 01_cover…）",
          files[0] == "01_cover.png" and len(files) >= 3
          and all(f.endswith(".png") for f in files), str(files))
    rows = _assets_of(aid1)
    check("assets 行数与文件数一致", len(rows) == len(files), f"{len(rows)} 行 / {len(files)} 文件")
    check("kind/尺寸/路径正确",
          rows[0].kind == "cover" and all(r.kind == "quote" for r in rows[1:])
          and all(r.width == 1080 and r.height == 1440
                  and r.path == f"assets/{aid1}/{fname}" for r, fname in zip(rows, files)),
          str([(r.kind, r.width, r.height, r.path) for r in rows]))
    with session_scope() as s:
        art = s.get(Article, aid1)
        check("article 仍为 ready（出图成功不落错）", art.status == "ready", art.status)

    print("\n[2] 中文无乱码无截断")
    s_txt = imaging.sanitize_text("标题🔥文案\n\t工厂✅👇")
    check("emoji/制表符剥离且保留换行",
          all(c not in s_txt for c in ("🔥", "✅", "👇", "\t")) and "\n" in s_txt, repr(s_txt))
    probe = Image.new("RGB", (16, 16))
    from PIL import ImageDraw

    d = ImageDraw.Draw(probe)
    slot = {"id": "quote", "font": {"size": 72, "weight": "regular", "line_height": 1.4},
            "shrink_to_fit": True,
            "_fonts": {"regular": "SourceHanSansSC-Regular.otf"}}
    long_text = "这是一句刻意构造的超长金句用来验证自动缩小字号而非截断的行为是否符合预期预期预期" * 2
    font, lines, lh = imaging._layout_text(d, long_text, slot, 840, 480)
    check("超长自动缩小字号而非截断", font.size < 72 and font.size >= config.IMAGING_MIN_FONT_SIZE,
          f"72 -> {font.size}，{len(lines)} 行")
    check("折行宽度不超槽位", all(d.textlength(ln, font=font) <= 840 for ln in lines))
    img = imaging.render("quote_card", {"quote": long_text, "footer": "AI与编程"})
    gray = img.convert("L")
    ink = sum(1 for p in gray.getdata() if p < 120 or p > 240)
    check("渲染非空白（有实际墨迹）", ink / (img.width * img.height) > 0.005,
          f"活跃像素占比 {ink / (img.width * img.height):.4f}")
    img.save(_TMP / "long_quote_sample.png")

    print("\n[3] 重复生成不残留（开新行，新目录干净，旧目录保留）")
    (art_dir / "junk.txt").write_text("残留", encoding="utf-8")  # 模拟脏目录
    resp2 = _gen(client, 1, "xhs")
    body2 = resp2.json()
    check("重新生成成功", body2.get("status") == "ready", str(body2))
    aid2 = body2["article_id"]
    check("开新行（id 不同）", aid2 != aid1, f"{aid1} -> {aid2}")
    files2 = sorted(p.name for p in (config.ASSETS_DIR / str(aid2)).iterdir())
    rows2 = _assets_of(aid2)
    check("新目录文件数与 assets 行数一致", len(files2) == len(rows2) >= 3, str(files2))
    check("新目录无残留外来文件", all(f.endswith(".png") for f in files2), str(files2))
    check("旧 article 目录保留（回溯，脏文件仍在）",
          (config.ASSETS_DIR / str(aid1) / "junk.txt").exists(),
          str(sorted(p.name for p in (config.ASSETS_DIR / str(aid1)).iterdir())))
    check("旧 article 的 assets 行保留", len(_assets_of(aid1)) == len(rows))
    # render_assets 幂等：同 article 重复渲染不重复登记
    with session_scope() as s:
        before = len(s.scalars(select(Asset).where(Asset.article_id == aid2)).all())
    with session_scope() as s:
        n = xhs_adapter.render_assets(s, aid2, "重复渲染封面", ["重复渲染金句一", "重复渲染金句二"],
                                      footer_text="AI与编程")
    with session_scope() as s:
        after = len(s.scalars(select(Asset).where(Asset.article_id == aid2)).all())
    check("render_assets 重复渲染替换旧行（不重复登记）", n == 3 and before == 4 and after == 3,
          f"render={n} before={before} after={after}")

    print("\n[4] wechat_cover 版式独立渲染（900×383）")
    r = imaging.render_wechat_cover("大模型价格战再起，开发者迎来红利", _TMP / "wechat_cover.png")
    wc = Image.open(_TMP / "wechat_cover.png")
    check("尺寸 900×383", wc.size == (900, 383), str(wc.size))
    check("返回值与文件一致", (r.width, r.height) == wc.size and r.kind == "cover")

    print("\n[5] checklist_card 版式独立渲染（不接生成链路）")
    img = imaging.render("checklist_card", {
        "title": "新人做账号三件事",
        "item_1": "先发 20 篇再谈数据",
        "item_2": "每天固定 30 分钟",
        "item_3": "只对标一个同行",
        "footer": "AI与编程",
    })
    img.save(_TMP / "checklist_sample.png")
    check("渲染成功且画布 1080×1440", img.size == (1080, 1440), str(img.size))

    print("\n[6] 字体缺失 → failed 且提示明确")
    real_fonts = config.FONTS_DIR
    imaging._FONT_CACHE.clear()  # 模拟冷启动进程（缓存内无字体）
    config.FONTS_DIR = _TMP / "no_fonts"
    resp3 = _gen(client, 2, "xhs")
    body3 = resp3.json()
    check("返回 200 + failed", resp3.status_code == 200 and body3.get("status") == "failed",
          str(body3))
    err = body3.get("error") or ""
    check("error 注明图文合成失败与字体缺失", "图文合成失败" in err and "data/fonts" in err, err)
    with session_scope() as s:
        art3 = s.get(Article, body3["article_id"])
        check("落 failed 行且无 assets（不留半成品）",
              art3.status == "failed" and _assets_of(art3.id) == [], art3.status)
        check("failed 行仍记 meta.usage", (art3.meta or {}).get("usage") is not None)
    config.FONTS_DIR = real_fonts
    imaging._FONT_CACHE.clear()

    print("\n[7] P0 回归（wechat 不出图）+ 400 不支持平台")
    rw = _gen(client, 2, "wechat")
    check("wechat 生成仍 ready", rw.status_code == 200 and rw.json().get("status") == "ready",
          str(rw.json()))
    wid = rw.json()["article_id"]
    check("wechat 不出图不登记 assets", _assets_of(wid) == []
          and not (config.ASSETS_DIR / str(wid)).exists())
    rbili = _gen(client, 1, "bilibili")
    check("400 不支持平台", rbili.status_code == 400, str(rbili.status_code))

    print("\n[8] imaging 单元补充")
    res = imaging.render_note_images("封面文案", ["金句一", "", "金句二"], _TMP / "notes")
    check("空金句跳过且编号连续",
          [r.filename for r in res] == ["01_cover.png", "02_quote.png", "03_quote.png"],
          str([r.filename for r in res]))
    out_dir = _TMP / "notes"
    (out_dir / "stale.txt").write_text("旧文件", encoding="utf-8")
    res2 = imaging.render_note_images("新封面", ["新金句"], out_dir)
    names = sorted(p.name for p in out_dir.iterdir())
    check("清旧文件生效（stale.txt 被清）", "stale.txt" not in names and len(names) == len(res2),
          str(names))
    try:
        imaging.render("no_such_template", {})
        check("未知版式抛 ImagingError", False)
    except imaging.ImagingError:
        check("未知版式抛 ImagingError", True)

    print("\n[9] 两段式封面：背景图铺底 + 蒙层；mock 高线不联网")
    check("LLM_MOCK 下 cover_background 返回 None（零联网）",
          imagegen.cover_background("标题", "正文", ["AI"], 1080, 1440) is None)
    check("LLM_MOCK 下提示词走确定性回退",
          "标题" in imagegen.cover_prompt("标题", "正文", ["AI"]))
    # 造一张亮色渐变当底图：渲染后封面不应再是版式纯色底，且文字区被压暗
    grad = Image.new("RGB", (864, 1152))
    for yy in range(1152):
        grad.paste(tuple(int(200 + 55 * yy / 1152) for _ in range(3)), (0, yy, 864, yy + 1))
    solid = imaging.render("emotion_cover", {"headline": "纯色对照"})
    with_bg = imaging.render("emotion_cover", {"headline": "纯色对照"}, background_image=grad)
    check("背景图改变封面像素（非纯色底）",
          list(solid.getdata()) != list(with_bg.getdata()))
    mid = with_bg.getpixel((540, 720))  # headline 槽位中心：蒙层后应比亮底暗
    bright = grad.resize((1080, 1440)).getpixel((540, 720))
    check("文字区蒙层压暗（对比度保障）", sum(mid) < sum(bright),
          f"{mid} vs {bright}")
    res3 = imaging.render_note_images("封面", ["金句"], _TMP / "notes_bg", cover_background=grad)
    check("金句图不吃背景图（仍纯色版式）",
          [r.filename for r in res3] == ["01_cover.png", "02_quote.png"])

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"FAIL：{len(FAILURES)} 项未通过 -> {FAILURES}")
        return 1
    print("PASS：P2 全部验收项通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("FAIL：脚本异常退出")
        raise SystemExit(1)
