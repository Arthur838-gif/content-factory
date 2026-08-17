"""小红书文案适配器（M7 文案部分，计划书 6.1 / SDD 5.6）。

把 XhsNote 格式化为可直接发布的文案：正文末尾空一行后拼 `#标签`，复制即可发；
meta 平台差异字段约定（计划书第 5 章）：cover_note ← cover_text，image_plan ← image_quotes。
纯文本处理，不调 LLM、不出图、不调外部接口。
"""
from dataclasses import dataclass, field

from ..schemas import XhsNote


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
