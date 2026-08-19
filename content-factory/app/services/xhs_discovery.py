"""小红书领域发现（P5d）：官方类目七日爆款 → 关键词推荐 + 对标账号。

数据来自 RedFox 七日爆款榜与账号搜索（按调用计费），只在建栏目表单里
由用户点按钮手动触发，不做定时任务；结果带内存缓存（同一类目 6 小时
内重复点击不重复计费，失败不缓存）。

- 关键词推荐：爆款笔记标题/正文里的 hashtag 按词频排序——真实在爆的
  词，比手写词表准且自带时效；hashtag 不足时用标题高频 CJK 二元组补位
  （无分词依赖的粗挖掘，只做候选不做判定）。
- 对标账号：按类目/关键词搜活跃账号（昵称、粉丝、作品数、简介），
  建栏目时参考内容方向。
"""
import logging
import re
from datetime import datetime

from .. import config
from ..collectors import redfox
from ..collectors.redfox import _to_int

logger = logging.getLogger(__name__)

_HASHTAG = re.compile(r"#([^\s#，。！？、.,!?;；:：]+)")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL_SECONDS = 6 * 3600


def _cached(key: str, fn):
    """成功结果缓存 6 小时；异常不缓存（重试即重新计费调用）。"""
    hit = _CACHE.get(key)
    now = datetime.now().timestamp()
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    value = fn()
    _CACHE[key] = (now, value)
    return value


def mine_keywords(notes: list[dict], limit: int = 20) -> list[str]:
    """从七日爆款笔记挖推荐关键词：hashtag 词频优先，不足补标题高频二元组。"""
    counter: dict[str, int] = {}
    for note in notes:
        if not isinstance(note, dict):
            continue
        text = f"{note.get('title') or ''} {note.get('desc') or ''}"
        for tag in _HASHTAG.findall(text):
            tag = tag.strip()
            if len(tag) >= 2:
                counter[tag] = counter.get(tag, 0) + 1
    keywords = [kw for kw, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    if len(keywords) < limit // 2:
        # hashtag 太少（该类目笔记不爱带 tag）→ 标题高频二元组补位，出现 ≥2 次才算
        bigrams: dict[str, int] = {}
        for note in notes:
            if not isinstance(note, dict):
                continue
            for run in _CJK_RUN.findall(note.get("title") or ""):
                for i in range(len(run) - 1):
                    gram = run[i : i + 2]
                    bigrams[gram] = bigrams.get(gram, 0) + 1
        extra = [g for g, c in sorted(bigrams.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 2]
        keywords.extend(g for g in extra if g not in keywords)
    return keywords[:limit]


def _note_view(note: dict) -> dict:
    ana = note.get("anaAdd") if isinstance(note.get("anaAdd"), dict) else {}
    likes = _to_int(ana.get("addLikeCount") or ana.get("useLikeCount"))
    return {
        "title": str(note.get("title") or "").strip(),
        "url": str(note.get("photoJumpUrl") or ""),
        "likes": likes,
        "public_time": str(note.get("publicTime") or ""),
    }


def category_insights(category: str) -> dict:
    """类目七日爆款 → {keywords, notes}；notes 按新增点赞倒序取前 10。"""
    def build() -> dict:
        notes = [n for n in redfox.seven_day_hot(category) if isinstance(n, dict)]
        views = sorted((_note_view(n) for n in notes), key=lambda v: (-v["likes"], v["title"]))
        return {
            "category": category,
            "keywords": mine_keywords(notes),
            # 空标题的笔记（纯视频/图集）对示例展示没意义，跳过
            "notes": [v for v in views if v["title"]][:10],
        }

    return _cached(f"insights:{category}", build)


def _account_view(account: dict) -> dict:
    return {
        "name": str(account.get("accountName") or "").strip(),
        "fans": _to_int(account.get("accountFans")),
        "works": _to_int(account.get("accountTotalWorks") or 0),
        "likes": _to_int(account.get("accountLikes")),
        "desc": str(account.get("accountDesc") or "").strip().replace("\n", " ")[:80],
    }


def benchmark_accounts(keyword: str, limit: int = 8) -> list[dict]:
    """按关键词搜对标账号（最热优先），建栏目参考。"""
    def build() -> list[dict]:
        rows = redfox.search_accounts(keyword, sort_type="_4")
        return [_account_view(a) for a in rows if str(a.get("accountName") or "").strip()][:limit]

    return _cached(f"accounts:{keyword}", build)


def discovery_ready() -> bool:
    """推荐词/对标账号都依赖 RedFox（无降级源，没 Key 就明确告知）。"""
    return bool(config.REDFOX_API_KEY)
