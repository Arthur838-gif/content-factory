"""M7 平台适配层（计划书第 7 章 M7 文案部分）。

适配器是纯文本处理：把 M5 产出的结构化 Schema 格式化为可直接发布的文案与
articles 行所需的 (title, content, tags, meta 平台差异字段)。不调 LLM、不出图、
不调外部接口；PIL 图文合成（P2）与素材包 ZIP（P3）到时在此扩展。
"""

from . import xhs  # noqa: F401

PLATFORM_ADAPTERS = {
    "xhs": xhs,
}
