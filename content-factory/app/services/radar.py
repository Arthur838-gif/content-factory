"""M3 选题雷达：领域关键词过滤 + 自动建候选选题（含撞题去重）+ 低粉爆款引擎（P-1b）。

P-1b 增量：
- 低粉爆款打分（纯规则，实时链路不调 LLM）与 viral_samples 落库
- 自动 / 人工样本共用的 xhs 判定管线 process_xhs_item
- 周度 LLM 拆解编排（复用 prompt_engine + generator，结论回写样本与标签库）

P9 增量：公众号爆款判定管线 process_gzh_item（优质库接口无粉丝字段，
用阅读量下限 + 互动密度替代「低粉」口径），evidence 快照带公众号指标。
"""
import logging
import re
from datetime import datetime, timedelta
from functools import lru_cache

from sqlalchemy import or_, select, update

from .. import config
from ..db import session_scope
from ..models import HotItem, TagLibrary, Topic, ViralSample
from ..schemas import ViralTeardown
from . import domain_service, generator, prompt_engine

logger = logging.getLogger(__name__)

_CJK = r"[\u4e00-\u9fff]"
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9]+")


def load_domains() -> dict[str, list[str]]:
    """领域词表（P-2 起存数据库，domains.yml 只是种子源）。委托 domain_service。"""
    return domain_service.load_domains()


def register_domain(domain: str, keywords: list[str]) -> bool:
    """注册/合并领域词表（兼容入口）：已有领域只追加缺失关键词。

    返回是否发生写入。建栏目路径请改用 domain_service.upsert_domain
    （与栏目插入同一事务）；本函数独立开事务提交。
    """
    try:
        with session_scope() as session:
            result = domain_service.upsert_domain(session, domain, keywords, source="user")
            return bool(result["created"] or result["added_keywords"])
    except ValueError as exc:
        logger.warning("领域登记被拒（%s）：%r", exc, domain)
        return False


def match_domain(title: str, domains: dict[str, list[str]] | None = None) -> tuple[str, str] | None:
    """返回 (领域, 命中关键词)；多领域命中取先声明者；未命中返回 None。

    domains 传入预载的领域表（批量采集时整轮只查一次库）；
    缺省现查（单条入口，如人工喂样本）。委托 domain_service。
    """
    return domain_service.match_domain(title, domains)


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


@lru_cache(maxsize=4096)
def _tokens_cached(text: str) -> frozenset[str]:
    return frozenset(tokenize(text))


def query_viral_samples(session, domain: str | None = None):
    """ViralSample × HotItem 联表（viral_score 倒序）——样本 API / 管理页 /
    校准视图三处共用同一查询形状；domain 过滤可选。调用方自行 limit。"""
    stmt = (
        select(ViralSample, HotItem)
        .join(HotItem, ViralSample.hot_item_id == HotItem.id)
        .order_by(ViralSample.viral_score.desc(), ViralSample.id.desc())
    )
    if domain:
        stmt = stmt.where(ViralSample.domain == domain)
    return stmt


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def radar_score(item: HotItem) -> float:
    """P-1a 基线分：命中即 1.0，榜单前 50 位次加权最高 +0.5。

    互动数据加权与阈值校准留给 P4（TODO(confirm)）。
    """
    rank = int((item.raw or {}).get("rank") or 0)
    return round(1.0 + max(0, 50 - rank) / 100, 2)


# ---- 低粉爆款（P-1b，第 6.2 节）----
def viral_score(item: HotItem) -> float:
    """爆文率 = (likes + 2×collects + 3×comments) ÷ max(fans, 1)。

    fans 为 0 或空时按 1 计（不除零）；该值只做确定性规则计算，实时链路不调 LLM。
    """
    fans = max(item.fans or 0, 1)
    return round((item.likes + 2 * item.collects + 3 * item.comments) / fans, 4)


def is_low_fans_viral(item: HotItem) -> bool:
    """低粉爆款判定：fans ≤ VIRAL_FANS_MAX 且 viral_score ≥ VIRAL_SCORE_MIN。

    VIRAL_LIKES_MIN 是自动采样候选的预筛（见 process_xhs_item），不进入本判定；
    fans 高于上限直接否决；阈值初值集中 config.py，校准推迟到 P4。
    """
    if (item.fans or 0) > config.VIRAL_FANS_MAX:
        return False
    return viral_score(item) >= config.VIRAL_SCORE_MIN


def process_xhs_item(
    session, item: HotItem, domain: str, keyword: str, auto: bool = True
) -> dict:
    """xhs 条目统一判定管线：入选 → viral_samples + 自动建题（含撞题去重）。

    auto=True（自动采样）：fans 未知（0）不判定、不伪造（降级只落笔记级数据）；
    且 likes 未达 VIRAL_LIKES_MIN 预筛的候选直接跳过判定。
    auto=False（人工喂样本）：fans 为显式录入值，直接判定，与自动样本同一管线。
    """
    result = {
        "viral": False,
        "viral_score": None,
        "viral_sample_id": None,
        "topic_outcome": None,
        "topic_id": None,
    }
    if auto and (item.fans or 0) <= 0:
        return result  # fans 探针结论 = 不可用：降级模式，只落 hot_items
    if auto and item.likes < config.VIRAL_LIKES_MIN:
        return result
    if not is_low_fans_viral(item):
        return result

    score = viral_score(item)
    sample = ViralSample(
        hot_item_id=item.id,
        domain=domain,
        viral_score=score,
        title_pattern="auto",
        reason="rule",
    )
    session.add(sample)
    session.flush()
    outcome, topic = create_or_merge_topic(session, item, domain, keyword, score=score, viral_score=score)
    result.update(
        viral=True,
        viral_score=score,
        viral_sample_id=sample.id,
        topic_outcome=outcome,
        topic_id=topic.id,
    )
    return result


# ---- 公众号爆款判定（P9；优质库接口无粉丝字段，改用阅读量下限 + 互动密度）----
def _gzh_article(item: HotItem) -> dict:
    """公众号条目的 raw.article（RedFox 原始字段：readCount/watchCount/shareCount…）。"""
    raw = item.raw or {}
    article = raw.get("article") if isinstance(raw.get("article"), dict) else {}
    return article


def _gzh_metric(article: dict, key: str) -> int:
    """raw.article 指标容错取整（文档为 Integer，兜底 "1,200" / "1.5万" 等字符串）。"""
    value = article.get(key)
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    from ..collectors.redfox import _to_int

    return _to_int(value)


def gzh_reads(item: HotItem) -> int:
    """公众号阅读数（判定分母；人工喂样本经详情接口取，字段同形）。"""
    return _gzh_metric(_gzh_article(item), "readCount")


def gzh_viral_score(item: HotItem) -> float:
    """公众号爆文率 = (likes + watches + 2×collects + 3×shares + 3×comments) ÷ max(reads, 1)。

    likes/collects/comments 入库时已映射到 ORM 列；reads/watches/shares 在
    raw.article。reads 为 0 或空时按 1 计（不除零）。纯规则计算，不调 LLM。
    """
    article = _gzh_article(item)
    engagement = (
        item.likes
        + _gzh_metric(article, "watchCount")
        + 2 * item.collects
        + 3 * _gzh_metric(article, "shareCount")
        + 3 * item.comments
    )
    reads = max(gzh_reads(item), 1)
    return round(engagement / reads, 4)


def is_gzh_viral(item: HotItem) -> bool:
    """公众号爆款判定：reads ≥ GZH_READS_MIN 且 gzh_viral_score ≥ GZH_SCORE_MIN。

    阅读量是互动密度的分母——下限属于指标有效性（几十阅读的高比例没有
    意义），不同于小红书 VIRAL_LIKES_MIN 的质量预筛，故直接进判定本体。
    """
    if gzh_reads(item) < config.GZH_READS_MIN:
        return False
    return gzh_viral_score(item) >= config.GZH_SCORE_MIN


def process_gzh_item(
    session, item: HotItem, domain: str, keyword: str, auto: bool = True
) -> dict:
    """gzh 条目统一判定管线：入选 → viral_samples + 自动建题（含撞题去重）。

    auto=True（自动采样）：readCount 缺失（0）不判定、不伪造（降级只落文章级数据）。
    auto=False（人工喂样本，URL 抓详情）：指标来自详情接口，直接判定。
    """
    result = {
        "viral": False,
        "viral_score": None,
        "viral_sample_id": None,
        "topic_outcome": None,
        "topic_id": None,
    }
    if auto and gzh_reads(item) <= 0:
        return result  # 阅读数缺失 = 指标不可用：降级模式，只落 hot_items
    if not is_gzh_viral(item):
        return result

    score = gzh_viral_score(item)
    sample = ViralSample(
        hot_item_id=item.id,
        domain=domain,
        viral_score=score,
        title_pattern="auto",
        reason="rule",
    )
    session.add(sample)
    session.flush()
    outcome, topic = create_or_merge_topic(session, item, domain, keyword, score=score, viral_score=score)
    result.update(
        viral=True,
        viral_score=score,
        viral_sample_id=sample.id,
        topic_outcome=outcome,
        topic_id=topic.id,
    )
    return result


def _item_desc(item: HotItem) -> str:
    """素材正文摘录：RedFox 洞察的 raw.article.desc / 公众号的 raw.article.summary /
    GitHub 的 raw.description；公众号摘要缺失时取正文开头。

    截 200 字进 evidence——reference_points 用它给生成提供真实内容依据，
    而不只是标题（模型只看标题只能自由发挥）。
    """
    raw = item.raw or {}
    article = raw.get("article") if isinstance(raw.get("article"), dict) else {}
    desc = str(
        article.get("desc") or article.get("summary") or raw.get("description") or ""
    ).strip()
    if not desc:
        # 公众号搜索列表自带全文：摘要缺失时取正文开头当摘录
        desc = str(article.get("content") or "").strip()[:200]
    return desc[:200]


def _evidence_snapshot(item: HotItem, domain: str, keyword: str, viral: float | None = None) -> dict:
    metrics = {
        "fans": item.fans,
        "likes": item.likes,
        "collects": item.collects,
        "comments": item.comments,
    }
    if item.source == "gzh":
        # 公众号判定与生成都依赖阅读量口径：reads/watches/shares 一并进快照
        article = _gzh_article(item)
        metrics.update(
            {
                "reads": _gzh_metric(article, "readCount"),
                "watches": _gzh_metric(article, "watchCount"),
                "shares": _gzh_metric(article, "shareCount"),
            }
        )
    snapshot = {
        "hot_item_id": item.id,
        "source": item.source,
        "title": item.title,
        "desc": _item_desc(item),
        "url": item.url,
        "author": item.author,
        "captured_at": (item.captured_at or datetime.now()).isoformat(timespec="seconds"),
        "domain": domain,
        "matched_keyword": keyword,
        "metrics": metrics,
    }
    if viral is not None:
        snapshot["viral_score"] = viral
    return snapshot


def create_or_merge_topic(
    session,
    item: HotItem,
    domain: str,
    keyword: str,
    score: float | None = None,
    viral_score: float | None = None,
) -> tuple[str, Topic]:
    """撞题去重（M3）：近 7 天 status≠archived 且未过期的选题里找最高重叠度。

    Jaccard ≥ 0.5 视为同一选题：不新建行，样本快照追加进 evidence、score 取较大值；
    < 0.5 才新建（source=radar，expires_at = created_at + 72h）。
    score 缺省用 P-1a 基线分；低粉爆款样本传 viral_score 作为选题分。
    pillar 选题（栏目排期，P5）不参与候选：系列内容标题天然相似，
    且素材证据绑定栏目节奏，被合并会破坏排期。
    """
    now = datetime.now()
    window_start = now - timedelta(days=config.TOPIC_DEDUP_WINDOW_DAYS)
    candidates = session.scalars(
        select(Topic).where(
            Topic.created_at >= window_start,
            Topic.status != "archived",
            Topic.source != "pillar",
            or_(Topic.expires_at.is_(None), Topic.expires_at > now),
        )
    ).all()

    new_tokens = tokenize(item.title)
    best: Topic | None = None
    best_sim = 0.0
    for candidate in candidates:
        # 一轮采集里每条新条目都要对全部候选算 Jaccard，候选标题重复分词是平方级浪费
        sim = jaccard(new_tokens, _tokens_cached(candidate.title))
        if sim > best_sim:
            best, best_sim = candidate, sim

    snapshot = _evidence_snapshot(item, domain, keyword, viral=viral_score)
    score = radar_score(item) if score is None else score

    if best is not None and best_sim >= config.TOPIC_JACCARD_THRESHOLD:
        merged_score = max(best.score or 0.0, score)
        # 必须构造全新容器：JSON 列的旧值若被原地改写，flush 时新旧相等不会发 UPDATE
        evidence = dict(best.evidence or {})
        evidence["items"] = list(evidence.get("items", [])) + [snapshot]
        if "base_score" in evidence:
            # P4 recompute 已把基线快照进 evidence：撞题合并抬高 score 时同步抬高快照，
            # 否则下次全量重算会把合并进来的分数打回旧基线（score 单调不降契约被破坏）。
            # 旧值可能含效果分，取 max 属保守合并（宁可偏高不回退）。
            evidence["base_score"] = max(evidence["base_score"], merged_score)
        best.evidence = evidence
        best.score = merged_score
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
    """hot_items 只保留 90 天（第 5 章，周清理任务物理删除）。

    被 viral_samples 引用的行跳过不删：viral_samples 永久保留且 hot_item_id 为
    非空外键（PRAGMA foreign_keys=ON），强删会 IntegrityError 回滚整轮清理。
    被引用的 hot_item 随样本一起保留，是两契约冲突下唯一无损解。
    """
    cutoff = datetime.now() - timedelta(days=config.HOT_ITEMS_RETENTION_DAYS)
    referenced = set(session.scalars(select(ViralSample.hot_item_id)).all())
    rows = session.query(HotItem).filter(HotItem.captured_at < cutoff).all()
    removed = 0
    for row in rows:
        if row.id in referenced:
            continue
        session.delete(row)
        removed += 1
    skipped = len(rows) - removed
    if skipped:
        logger.info("跳过 %s 条被 viral_samples 引用的 hot_items（样本永久保留）", skipped)
    return removed


# ---- 周度 LLM 拆解（P-1b，附录 A3；唯一允许调 LLM 的雷达链路）----
def bump_tag(session, domain: str, tag: str, heat: int = 1) -> TagLibrary:
    """tag_library 累计热度（domain + tag 唯一，存在即 +heat）。"""
    row = session.scalars(
        select(TagLibrary).where(TagLibrary.domain == domain, TagLibrary.tag == tag)
    ).first()
    if row is None:
        row = TagLibrary(domain=domain, tag=tag, heat=0)
        session.add(row)
        session.flush()
    row.heat = (row.heat or 0) + heat
    return row


def _fallback_reason(teardown: ViralTeardown) -> str:
    parts = []
    if teardown.title_patterns:
        parts.append("标题模式：" + "；".join(teardown.title_patterns[:3]))
    if teardown.emotion_words:
        parts.append("情绪词：" + "、".join(teardown.emotion_words[:6]))
    if teardown.structures:
        parts.append("结构：" + "；".join(teardown.structures[:2]))
    return "｜".join(parts) or "LLM 拆解无有效结论"


def run_weekly_teardown(db_path=None) -> dict:
    """周度拆解：当周 viral_samples 交 LLM（A3 模板）总结模式，结论回写样本与标签库。

    由调度器每周触发一次；手动触发接口仅调试用。复用 prompt_engine 选模板
    与 generator 的 JSON 解析、重试、usage 记账；无 Key 走 mock 分支。
    """
    week_start = datetime.now() - timedelta(days=7)
    with session_scope(db_path) as session:
        samples = session.scalars(
            select(ViralSample).where(ViralSample.created_at >= week_start).order_by(ViralSample.id)
        ).all()
        summary = {"samples": len(samples), "reasons_updated": 0, "tags_bumped": 0, "usage": None}
        if not samples:
            logger.info("本周无 viral_samples，拆解跳过")
            return summary

        digest = []
        for sample in samples[:50]:
            item = session.get(HotItem, sample.hot_item_id)
            digest.append(
                {
                    "hot_item_id": sample.hot_item_id,
                    "domain": sample.domain,
                    "title": item.title if item else "",
                    "likes": item.likes if item else 0,
                    "collects": item.collects if item else 0,
                    "comments": item.comments if item else 0,
                    "fans": item.fans if item else 0,
                    "viral_score": sample.viral_score,
                }
            )

        _prompt, system_msg, user_msg = prompt_engine.render_messages(
            session,
            "xhs",
            "teardown",
            {
                "week_range": f"{week_start:%m-%d} ~ {datetime.now():%m-%d}",
                "samples": digest,
            },
        )
        # 拆解产物是模式总结而非可发布文案，引用的爆款原文可能带平台敏感词
        # （词表面向成文闸门），故豁免成文敏感词检查（唯一豁免点，见 SDD 8.1）
        result = generator.generate("xhs", ViralTeardown, system_msg, user_msg, check_sensitive=False)
        if result.article is None:
            raise RuntimeError(f"周度拆解生成失败：{result.error}")
        teardown = result.article
        summary["usage"] = result.usage

        by_id = {s.hot_item_id: s for s in teardown.samples}
        for sample in samples:
            hit = by_id.get(sample.hot_item_id)
            if hit and (hit.reason or hit.title_pattern):
                if hit.reason:
                    sample.reason = hit.reason
                if hit.title_pattern:
                    sample.title_pattern = hit.title_pattern
                summary["reasons_updated"] += 1

        if summary["reasons_updated"] == 0:
            # LLM 未逐条映射（mock 分支即如此）：按聚合结论回写全部当周样本
            reason = _fallback_reason(teardown)
            pattern = teardown.title_patterns[0] if teardown.title_patterns else "auto"
            for sample in samples:
                sample.reason = reason
                if sample.title_pattern in (None, "", "auto"):
                    sample.title_pattern = pattern
            summary["reasons_updated"] = len(samples)

        for tag in teardown.tags:
            bump_tag(session, tag.domain, tag.tag)
            summary["tags_bumped"] += 1

        logger.info(
            "周度拆解完成：samples=%s reasons_updated=%s tags_bumped=%s",
            summary["samples"], summary["reasons_updated"], summary["tags_bumped"],
        )
        return summary
