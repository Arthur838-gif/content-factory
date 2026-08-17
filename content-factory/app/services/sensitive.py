"""敏感词过滤（计划书第 6.2 节，双端词表分离的共享服务）。

公众号词表 P0 起用；小红书词表 P1 接入（sensitive_xhs.txt）。词表即数据：
词表存文件不写进代码，改词表不改代码。每次检测现读文件，词表改动立即生效，
与 prompt_engine 的"模板不进程缓存"同构。
"""
import logging

from .. import config

logger = logging.getLogger(__name__)

# 平台 → 词表路径；新平台在此追加一项即可，不动 M5 调用方。
_WORDLIST_PATHS: dict[str, "object"] = {
    "wechat": config.SENSITIVE_FILE_WECHAT,
    "xhs": config.SENSITIVE_FILE_XHS,
}


def _wordlist_path(platform: str):
    path = _WORDLIST_PATHS.get(platform)
    if path is None:
        raise ValueError(f"未配置平台 {platform} 的敏感词表路径")
    return path


def load_words(platform: str) -> list[str]:
    """读取词表文件（每行一个词；# 开头注释、空行忽略）。文件不存在视为空表。

    每次现读，不进程缓存——审核失败回填的新词无需重启即对下次生成生效。
    """
    path = _wordlist_path(platform)
    if not path.exists():
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(line)
    return words


def find_hits(text: str, platform: str) -> list[str]:
    """返回命中的敏感词列表（按词表声明顺序、去重）；空表返回空列表。

    纯子串匹配：词表规模百级，线性扫描足够，无需引 AC 自动机等依赖。
    """
    words = load_words(platform)
    if not words:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in text and word not in seen:
            hits.append(word)
            seen.add(word)
    return hits
