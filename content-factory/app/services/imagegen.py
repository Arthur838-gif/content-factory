"""封面底图生成（两段式封面方案的第一段：GLM cogview-4 文生图）。

链路：文案（标题/正文/标签）→ LLM 归纳一句"画面提示词"（与文案同步）
→ cogview-4 生成无字底图 → PIL（imaging.render）叠印中文标题。

纪律：
- 只出"无字底图"，图上文字一律由 imaging 用思源黑体叠印（图像模型中文
  大字直出不可靠，A3 实测结论）；
- 本模块任何失败（无 key / 超时 / 接口报错）都返回 None，调用方回退到
  版式纯色底——出图增强绝不把 article 拖成 failed；
- 出图模型经 model_config.resolve(image) 解析（/models 页「当前使用」>
  .env 回退），与文案模型互独立；封面提示词归纳仍走文案模型；
- CF_IMAGEGEN_ENABLED 是防误计费的总闸；总闸关或生效配置无 key 即跳过
  真实出图，测试链路零联网。
"""
import io
import json
import logging

import httpx
from PIL import Image

from .. import config
from . import generator, model_config

logger = logging.getLogger(__name__)

# cogview-4 支持的尺寸档；3:4 竖版对应小红书封面画布（1080x1440 同比例）
_XHS_COVER_SIZE = "864x1152"

_PROMPT_SYSTEM = (
    "你是小红书封面配图师。根据笔记内容归纳一句画面提示词，用于文生图模型。"
    '只输出 JSON：{"prompt": "一句话提示词"}。'
)
_PROMPT_RULES = (
    "要求：画面具体可画（场景/主体/氛围/光线/风格），贴合笔记主题；"
    "构图上下留白（中间偏下叠标题用）；画面里不要出现任何文字、水印、人名；"
    "20-60 字，中文。"
)


def cover_prompt(title: str, content: str, tags: list[str]) -> str:
    """从文案内容归纳画面提示词；LLM 失败时退回标题+标签的确定性提示词。"""
    fallback = f"小红书笔记背景图，主题：{title}，风格：{'、'.join(tags[:3])}，画面精致有氛围感，无文字"
    if model_config.mock_enabled(model_config.PURPOSE_TEXT):
        return fallback
    user = (
        f"笔记标题：{title}\n标签：{'、'.join(tags[:6])}\n"
        f"正文节选：{(content or '')[:600]}\n\n{_PROMPT_RULES}"
    )
    try:
        raw, _usage = generator._call_llm(_PROMPT_SYSTEM, user)
        text = str((json.loads(raw) or {}).get("prompt", "")).strip()
        return text or fallback
    except Exception as exc:
        logger.warning("封面提示词归纳失败，退回确定性提示词：%s", exc)
        return fallback


def generate_background(prompt: str, width: int, height: int) -> Image.Image | None:
    """文生图返回 PIL 图（按画布比例取尺寸档再缩放）；失败返回 None。

    图片模型走 model_config.resolve(image)（/models 页「当前使用」> .env 回退）；
    CF_IMAGEGEN_ENABLED 是防误计费总闸，关掉或生效配置无 key 一律跳过。
    """
    if not config.IMAGEGEN_ENABLED or model_config.mock_enabled(model_config.PURPOSE_IMAGE):
        return None
    llm = model_config.resolve(model_config.PURPOSE_IMAGE)
    size = _pick_size(width, height)
    url = f"{llm.base_url}/images/generations"
    try:
        with httpx.Client(timeout=config.IMAGEGEN_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {llm.api_key}"},
                json={"model": llm.model, "prompt": prompt,
                      "size": size, "n": 1},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
            img_url = data[0].get("url") if data and isinstance(data[0], dict) else ""
            if not img_url:
                raise ValueError("响应里没有图片 URL")
            img_bytes = client.get(img_url).content
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if img.size != (width, height):
            img = img.resize((width, height), Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("封面底图生成失败，回退纯色版式：%s", exc)
        return None


_SIZE_TABLE = {
    0.75: "864x1152",   # 3:4 小红书封面
    0.57: "768x1344",   # 4:7 更瘦长
    1.0: "1024x1024",
    2.0: "1440x720",
}


def _pick_size(width: int, height: int) -> str:
    ratio = round(width / height, 2)
    return min(_SIZE_TABLE.items(), key=lambda kv: abs(kv[0] - ratio))[1]


def cover_background(title: str, content: str, tags: list[str],
                     width: int, height: int) -> Image.Image | None:
    """组合入口：文案 → 提示词 → 底图。给 routes_topics 在出图事务前调用。"""
    return generate_background(cover_prompt(title, content, tags), width, height)
