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


# 单次追加的词数与单词长度上限：词表是子串匹配，超短词（如单字母）会
# 误杀整站文章，超长串没有匹配意义——都从入口拦掉
_MAX_BATCH_WORDS = 50
_MAX_WORD_CHARS = 50


def add_words(platform: str, words: list[str]) -> tuple[list[str], list[str]]:
    """把命中词追加进平台词表文件（滚动扩充，文件头注释承诺的回填路径）。

    去重（对现有词表 + 本批次内部）、去空白、跳过 # 开头与超限词；
    load_words 每次现读，写入即生效（无需重启）。返回 (added, skipped)。
    """
    path = _wordlist_path(platform)
    existing = set(load_words(platform))
    added: list[str] = []
    skipped: list[str] = []
    for raw in words:
        word = (raw or "").strip()
        if not word or word.startswith("#") or len(word) > _MAX_WORD_CHARS:
            skipped.append(word)
            continue
        if word in existing:
            skipped.append(word)
            continue
        existing.add(word)
        added.append(word)
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        with path.open("a", encoding="utf-8") as fh:
            if text and not text.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(added) + "\n")
        logger.info("敏感词表[%s] 追加 %d 词：%s", platform, len(added), "、".join(added))
    return added, skipped
