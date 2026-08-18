"""小红书适配器（M7，计划书 6.1 / 6.3 / SDD 5.6）。

文案部分：把 XhsNote 格式化为可直接发布的文案——正文末尾空一行后拼 `#标签`，复制即可发；
meta 平台差异字段约定（计划书第 5 章）：cover_note ← cover_text，image_plan ← image_quotes。

图片部分（P2）：render_assets 调共享图文服务 services/imaging.py 出图（封面 + 金句图）
并登记 assets 表；本模块不自己画图（SDD 3.1 归属共享层）、不调 LLM、不调小红书接口。
"""
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import config
from ..models import Asset
from ..schemas import XhsNote
from ..services import imaging


@dataclass
class FormattedArticle:
    """适配产物：直接填进 articles 行的 title / content / tags / meta（不含 usage）。"""

    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def format_note(note: XhsNote) -> FormattedArticle:
    """XhsNote → 可发布文案 + meta（cover_note、image_plan）。"""
    # 标签去 #、去空白、去重后保序拼成 "#a #b #c"
    tags: list[str] = []
    for raw in note.tags:
        tag = raw.strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)

    content = note.content.rstrip()
    if tags:
        content += "\n\n" + " ".join(f"#{t}" for t in tags)

    meta = {
        "cover_note": note.cover_text,
        "image_plan": list(note.image_quotes),
    }
    return FormattedArticle(title=note.title, content=content, tags=tags, meta=meta)


def render_assets(
    session: Session,
    article_id: int,
    cover_note: str,
    image_plan: list[str],
    footer_text: str = "",
    cover_background=None,
) -> int:
    """M7 出图（P2）：渲染 1 张封面 + N 张金句图并登记 assets 行。

    在调用方的落库事务内执行（SDD 5.7：articles 与 assets 同一事务，不允许有文章
    无资产的半成品）；imaging 抛 ImagingError 时由调用方把 article 落 failed。
    cover_background 是两段式封面的第一段产物（cogview-4 底图，PIL Image 或
    None——生成失败/关闭开关时的回退就是纯色版式），本模块只透传不生成。
    重复渲染幂等：先清 data/assets/{article_id}/ 旧文件，再删旧 assets 行重建
    （衍生资产可重建；归档行的产物由其独立目录保留，不受影响）。
    """
    out_dir: Path = config.ASSETS_DIR / str(article_id)
    rendered = imaging.render_note_images(
        cover_note, image_plan, out_dir, footer_text=footer_text,
        cover_background=cover_background,
    )
    session.execute(delete(Asset).where(Asset.article_id == article_id))
    for r in rendered:
        session.add(
            Asset(
                article_id=article_id,
                kind=r.kind,
                path=f"assets/{article_id}/{r.filename}",
                width=r.width,
                height=r.height,
            )
        )
    return len(rendered)
