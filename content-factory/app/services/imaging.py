"""共享图文服务（计划书 6.3 / SDD 3.1：M6 与 M7 共用，归属共享层）。

版式即数据：画布/背景/字体/字号/槽位/色值全部在 data/imaging_templates/*.yml，
本模块只读配置渲染，不含业务逻辑、不调 LLM、不读写业务表。

渲染纪律（P2 任务四件套拍板）：
- 字体只从 data/fonts/ 加载（禁止系统字体路径），FontLoader 进程内缓存（SDD 6.1）；
- 文案超长自动缩小字号而非截断（下限 config.IMAGING_MIN_FONT_SIZE）；
- 图上文案渲染前剥离 emoji 与控制字符（PIL 无彩色 emoji 字形，防豆腐块）；
- 出图失败抛 ImagingError，由调用方决定落库语义（SDD 5.7：出图失败 article 整体 failed）。
"""
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from .. import config

logger = logging.getLogger(__name__)


class ImagingError(RuntimeError):
    """图文合成失败（字体缺失/版式损坏/写盘失败等），调用方落 failed 并注明原因。"""


@dataclass
class RenderedImage:
    """一张渲染产物：kind（cover/quote）、文件名、画布尺寸。"""

    kind: str
    filename: str
    width: int
    height: int


# 字体进程内缓存：键 (文件名, 字号)，避免每张图重读 16MB 字体文件（SDD 6.1）
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# 剥离区间：控制字符（保留 \n）、箭头/杂项符号/装饰符号区、变体选择符、ZWJ、emoji 区
_STRIP_RANGES = (
    (0x00, 0x09),
    (0x0B, 0x1F),
    (0x7F, 0x7F),
    (0x200D, 0x200D),
    (0x2190, 0x2BFF),  # 箭头、杂项符号、装饰符号（含 0x2600-0x27BF emoji）
    (0xFE00, 0xFE0F),  # 变体选择符（1️⃣ 等的组合符）
    (0xFEFF, 0xFEFF),
    (0x1F000, 0x1FBFF),  # 表情符号区（🥲🔥💡👇 及旗帜）
)


def sanitize_text(text: str) -> str:
    """剥离图上无法安全渲染的字符（emoji、变体选择符、控制字符），保留换行。"""
    out = []
    for ch in text:
        if ch == "\t":
            out.append(" ")
            continue
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _STRIP_RANGES):
            continue
        out.append(ch)
    return "".join(out)


def _load_font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(size))
    key = (filename, size)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path = config.FONTS_DIR / filename
    if not path.exists():
        raise ImagingError(
            f"data/fonts/ 缺少字体文件 {filename}，请按 docs/p2-task.md 放置思源黑体"
        )
    try:
        font = ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise ImagingError(f"字体文件无法加载 {filename}：{exc}") from exc
    _FONT_CACHE[key] = font
    return font


def load_template(name: str) -> dict:
    """读取版式 YAML。每次现读不缓存，改版式不重启即生效（与模板热更新同约定）。"""
    path = config.IMAGING_TEMPLATES_DIR / f"{name}.yml"
    if not path.exists():
        raise ImagingError(f"版式不存在：{path}")
    try:
        tpl = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ImagingError(f"版式 YAML 损坏 {path}：{exc}") from exc
    if not isinstance(tpl, dict) or "canvas" not in tpl or "slots" not in tpl:
        raise ImagingError(f"版式缺少 canvas/slots 字段：{path}")
    canvas = tpl["canvas"]
    # 提前拦住缺 width/height 的坏版式：留到 render 里就是裸 KeyError 逃逸
    if (
        not isinstance(canvas, dict)
        or not isinstance(canvas.get("width"), int)
        or not isinstance(canvas.get("height"), int)
    ):
        raise ImagingError(f"版式 canvas 缺少整数 width/height：{path}")
    return tpl


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度逐字折行（中英混排安全）；尊重文本自带换行。"""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if not cur or draw.textlength(cur + ch, font=font) <= max_width:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _layout_text(draw, text: str, slot: dict, box_w: int, box_h: int):
    """选字号 + 折行。shrink_to_fit 时超 high 先降字号再放行，绝不截断。"""
    fconf = slot.get("font") or {}
    weight = fconf.get("weight", "regular")
    tpl_fonts = slot.get("_fonts") or {}
    filename = tpl_fonts.get(weight, tpl_fonts.get("regular"))
    if not filename:
        raise ImagingError(f"槽位 {slot.get('id')} 所在版式未声明 font 文件")
    size = int(fconf.get("size", 48))
    line_height = float(fconf.get("line_height", 1.3))
    min_size = config.IMAGING_MIN_FONT_SIZE
    while True:
        font = _load_font(filename, size)
        lines = _wrap_lines(draw, text, font, box_w)
        total_h = int(size * line_height) * len(lines)
        if total_h <= box_h or not slot.get("shrink_to_fit") or size <= min_size:
            return font, lines, int(size * line_height)
        size = max(min_size, size - 4)


def render(name: str, texts: dict[str, str]) -> Image.Image:
    """按版式渲染一张图。texts 按 slot id 注入；slot 自带 text 字面量优先。"""
    tpl = load_template(name)
    canvas = tpl["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    bg = (tpl.get("background") or {}).get("color", "#ffffff")
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    fonts_conf = tpl.get("font") or {}

    for slot in tpl["slots"]:
        sid = slot.get("id", "")
        stype = slot.get("type")
        box = slot.get("box") or {}
        x, y = int(box.get("x", 0)), int(box.get("y", 0))
        bw, bh = int(box.get("width", 0)), int(box.get("height", 0))
        if stype == "rect":
            draw.rectangle([x, y, x + bw, y + bh], fill=slot.get("color", "#000000"))
            continue
        if stype != "text":
            raise ImagingError(f"未知槽位类型 {stype}（版式 {name} / 槽位 {sid}）")

        text = slot["text"] if "text" in slot else texts.get(sid, "")
        text = sanitize_text(str(text)).strip()
        if not text:
            continue

        if slot.get("badge"):
            draw.rectangle([x, y, x + bw, y + bh], fill=slot["badge"])

        slot_ctx = dict(slot)
        slot_ctx["_fonts"] = fonts_conf
        font, lines, line_h = _layout_text(draw, text, slot_ctx, bw, bh)
        fconf = slot.get("font") or {}
        color = fconf.get("color", "#1a1b25")
        align = slot.get("align", "left")
        valign = slot.get("valign", "top")
        total_h = line_h * len(lines)
        ty = y + (bh - total_h) // 2 if valign == "middle" else y
        for i, line in enumerate(lines):
            lw = draw.textlength(line, font=font)
            tx = x + (bw - lw) // 2 if align == "center" else x
            draw.text((tx, ty + i * line_h), line, font=font, fill=color)
    return img


def _clear_dir(out_dir: Path) -> None:
    """重复渲染清旧：清空目录内全部旧文件（计划书 6.3），目录本身保留。"""
    if out_dir.exists():
        for old in out_dir.iterdir():
            if old.is_file() or old.is_symlink():
                old.unlink()
            elif old.is_dir():
                shutil.rmtree(old)


def render_note_images(
    cover_text: str,
    quotes: list[str],
    out_dir: str | Path,
    footer_text: str = "",
) -> list[RenderedImage]:
    """渲染一组小红书配图：1 张封面 + N 张金句图，按上传顺序编号 01_cover.png…

    编号全局递增（01_cover、02_quote、03_quote…）；失败时清理半成品目录再抛
    ImagingError，保证目录里不留残缺文件。
    """
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        _clear_dir(out_dir)
        texts_common = {"footer": sanitize_text(footer_text).strip()}
        plan = []
        if cover_text and str(cover_text).strip():
            plan.append(("cover", config.IMAGING_COVER_TEMPLATE, str(cover_text)))
        for q in quotes or []:
            if q and str(q).strip():
                plan.append(("quote", config.IMAGING_QUOTE_TEMPLATE, str(q)))
        results: list[RenderedImage] = []
        for idx, (kind, tpl_name, text) in enumerate(plan, start=1):
            img = render(tpl_name, {"headline": text, "quote": text, **texts_common})
            w, h = img.size  # 画布尺寸直接取自产物，不再二次 load_template
            filename = f"{idx:02d}_{'cover' if kind == 'cover' else 'quote'}.png"
            try:
                img.save(out_dir / filename, format="PNG")
            finally:
                img.close()
            results.append(
                RenderedImage(kind=kind, filename=filename, width=w, height=h)
            )
        return results
    except ImagingError:
        _clear_dir(out_dir)
        raise
    except OSError as exc:
        _clear_dir(out_dir)
        raise ImagingError(f"图片写盘失败：{exc}") from exc
    except Exception as exc:
        # 版式字段缺失等意外错误统一收敛成 ImagingError：
        # 调用方（routes_topics）只认 ImagingError，裸 KeyError 会导致 500 而非 failed 落库
        _clear_dir(out_dir)
        raise ImagingError(f"图文合成意外失败：{exc}") from exc


def render_wechat_cover(text: str, out_path: str | Path) -> RenderedImage:
    """渲染公众号封面 900×383（P2 独立验证用；正式调用方是 M6）。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = render("wechat_cover", {"headline": text})
    out_path.unlink(missing_ok=True)
    try:
        img.save(out_path, format="PNG")
    finally:
        img.close()
    return RenderedImage(
        kind="cover", filename=out_path.name, width=img.width, height=img.height
    )
