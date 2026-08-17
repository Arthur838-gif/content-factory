"""M3 选题雷达（P-1a 子集）：领域关键词过滤 + 自动建候选选题（含撞题去重）。

低粉爆款打分（viral_samples）与周度拆解属 P-1b / M3 全量，不在本阶段。
"""
import logging
import re
from datetime import datetime, timedelta

import yaml
from sqlalchemy import or_, select, update

from .. import config
from ..models import HotItem, Topic

logger = logging.getLogger(__name__)

_CJK = r"[\u4e00-\u9fff]"
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9]+")


def load_domains() -> dict[str, list[str]]:
    """领域关键词表 data/domains.yml（改词表不改代码，每次采集现读）。

    两种写法等价：
      领域名: {keywords: [..]}   预留每领域附加配置
      领域名: [..]              纯关键词列表
    """
    data = yaml.safe_load(config.DOMAINS_FILE.read_text(encoding="utf-8")) or {}
    domains = data.get("domains") or {}
    result: dict[str, list[str]] = {}
    for domain, spec in domains.items():
        keywords = (spec or {}).get("keywords", []) if isinstance(spec, dict) else (spec or [])
        result[str(domain)] = [str(kw) for kw in keywords]
    return result


def match_domain(title: str) -> tuple[str, str] | None:
    """返回 (领域, 命中关键词)；多领域命中取 YAML 中先声明者；未命中返回 None。"""
    text = title.lower()
    for domain, keywords in load_domains().items():
        for keyword in keywords:
            if keyword.lower() in text:
                return domain, keyword
    return None


def tokenize(text: str) -> set[str]:
    """简单二元切分（计划书 M3：jieba 或简单二元切分，MVP 不引分词依赖）。

    CJK 连续段切字符 bigram；拉丁字母/数字连续段整词小写。
    """
    tokens: set[str] = set()
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    tokens.update(_LATIN_WORD.findall(text.lower()))
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def radar_score(item: HotItem) -> float:
    """P-1a 基线分：命中即 1.0，榜单前 50 位次加权最高 +0.5。

    互动数据加权与阈值校准留给 P-1b / P4（TODO(confirm)）。
    """
    rank = int((item.raw or {}).get("rank") or 0)
    return round(1.0 + max(0, 50 - rank) / 100, 2)


def _evidence_snapshot(item: HotItem, domain: str, keyword: str) -> dict:
    return {
        "hot_item_id": item.id,
        "source": item.source,
        "title": item.title,
        "url": item.url,
        "captured_at": (item.captured_at or datetime.now()).isoformat(timespec="seconds"),
        "domain": domain,
        "matched_keyword": keyword,
        "metrics": {
            "fans": item.fans,
            "likes": item.likes,
            "collects": item.collects,
            "comments": item.comments,
        },
    }


def create_or_merge_topic(session, item: HotItem, domain: str, keyword: str) -> tuple[str, Topic]:
    """撞题去重（M3）：近 7 天 status≠archived 且未过期的选题里找最高重叠度。

    Jaccard ≥ 0.5 视为同一选题：不新建行，样本快照追加进 evidence、score 取较大值；
    < 0.5 才新建（source=radar，expires_at = created_at + 72h）。
    """
    now = datetime.now()
    window_start = now - timedelta(days=config.TOPIC_DEDUP_WINDOW_DAYS)
    candidates = session.scalars(
        select(Topic).where(
            Topic.created_at >= window_start,
            Topic.status != "archived",
            or_(Topic.expires_at.is_(None), Topic.expires_at > now),
        )
    ).all()

    new_tokens = tokenize(item.title)
    best: Topic | None = None
    best_sim = 0.0
    for candidate in candidates:
        sim = jaccard(new_tokens, tokenize(candidate.title))
        if sim > best_sim:
            best, best_sim = candidate, sim

    snapshot = _evidence_snapshot(item, domain, keyword)
    score = radar_score(item)

    if best is not None and best_sim >= config.TOPIC_JACCARD_THRESHOLD:
        # 必须构造全新容器：JSON 列的旧值若被原地改写，flush 时新旧相等不会发 UPDATE
        evidence = dict(best.evidence or {})
        evidence["items"] = list(evidence.get("items", [])) + [snapshot]
        best.evidence = evidence
        best.score = max(best.score or 0.0, score)
        logger.info("撞题合并进 topic #%s（重叠度 %.2f）：%s", best.id, best_sim, item.title)
        return "merged", best

    topic = Topic(
        title=item.title,
        angle=f"{domain}·{keyword}",
        domain=domain,
        source="radar",
        status="new",
        score=score,
        evidence={"items": [snapshot]},
        expires_at=now + timedelta(hours=config.TOPIC_TTL_HOURS),
        created_at=now,
    )
    session.add(topic)
    session.flush()
    logger.info("自动创建候选选题 topic #%s：%s", topic.id, topic.title)
    return "created", topic


def archive_expired_topics(session) -> int:
    """到期且仍为 new 的 radar 选题置为 archived（第 5 章，每小时任务）。"""
    result = session.execute(
        update(Topic)
        .where(Topic.status == "new", Topic.expires_at.is_not(None), Topic.expires_at <= datetime.now())
        .values(status="archived")
    )
    return result.rowcount or 0


def cleanup_hot_items(session) -> int:
    """hot_items 只保留 90 天（第 5 章，周清理任务物理删除）。"""
    cutoff = datetime.now() - timedelta(days=config.HOT_ITEMS_RETENTION_DAYS)
    rows = session.query(HotItem).filter(HotItem.captured_at < cutoff).all()
    for row in rows:
        session.delete(row)
    return len(rows)
