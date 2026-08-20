"""P4 数据飞轮：评分重算与派生报表（确定性计算，实时链路不调 LLM）。

三块职责：
- topics.score 幂等重算（recompute）：score = base_score + 效果分，
  公式与参数拍板记录见 docs/p4-calibration.md；publish 回填成功后调用一次，
  也可手动全量重算。base_score 首次重算时快照进 evidence，此后不变。
- 模板效果分（prompt_stats）：派生报表，实时按 prompt 版本聚合已发布文章互动均值，
  不落字段、不自动启停模板（published < PROMPT_STATS_MIN_SAMPLES 只展示）。
- 成本与校准视图（cost_report / article_cost / calibration_view）：
  汇总 meta.usage 的估算成本；展示 viral_samples 判定 × 实际发布效果交叉表，
  供周四校准会人工拍板（系统不自动改阈值）。
"""
import logging
import math
import re
from datetime import datetime

from sqlalchemy import func, select

from .. import config
from ..db import session_scope
from ..models import Article, Prompt, PublishRecord, Topic, ViralSample
from . import model_config, radar

logger = logging.getLogger(__name__)

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def normalize_month(month: str | None) -> str | None:
    """month 缺省 → 当月；形如 YYYY-MM 且为真实月份 → 原样返回；否则 None。

    cost API 与 /stats 页面共用，保证"9999-99"这类值在两个入口都被 422。
    """
    if month is None:
        return datetime.now().strftime("%Y-%m")
    return month if _MONTH_RE.match(month) else None


def engagement(metrics: dict | None) -> float:
    """单条回填的加权互动：likes×W_L + collects×W_C + comments×W_M
    + watches×W_W + shares×W_S（缺项按 0）。

    watches/shares 是公众号回填字段（P9）；xhs 旧记录无这两键不受影响。
    reads 不进效果分：它是受众规模量纲（同 xhs 的 fans），不是互动强度。
    """
    m = metrics or {}
    return (
        float(m.get("likes", 0) or 0) * config.SCORE_W_LIKES
        + float(m.get("collects", 0) or 0) * config.SCORE_W_COLLECTS
        + float(m.get("comments", 0) or 0) * config.SCORE_W_COMMENTS
        + float(m.get("watches", 0) or 0) * config.SCORE_W_WATCHES
        + float(m.get("shares", 0) or 0) * config.SCORE_W_SHARES
    )


def effect_score(total_engagement: float) -> float:
    """效果分 = SCALE × log1p(总互动)：对数压缩，避免单篇爆文压制全部排序。"""
    return config.SCORE_EFFECT_SCALE * math.log1p(max(total_engagement, 0.0))


def _records_by_topic(session) -> dict[int, list[PublishRecord]]:
    """publish_records 按 topic 分组（经 article 归因；article_id 不漂移）。"""
    articles = {a.id: a for a in session.scalars(select(Article))}
    grouped: dict[int, list[PublishRecord]] = {}
    for record in session.scalars(select(PublishRecord).order_by(PublishRecord.id)):
        article = articles.get(record.article_id)
        if article is not None:
            grouped.setdefault(article.topic_id, []).append(record)
    return grouped


def recompute(db_path=None) -> dict:
    """全量幂等重算 topics.score：任何时候重跑结果一致；补录后重跑即按新数据更新。

    无回填数据的选题 score = base_score = 落库时评分（不拉低未发布选题）。
    每次重算把归因凭据（publish_record_max_id / 互动合计 / 时间）写进
    evidence["score_recompute"]，保证结果可追溯。
    """
    with session_scope(db_path) as session:
        by_topic = _records_by_topic(session)
        max_record_id = session.scalars(select(func.max(PublishRecord.id))).one() or 0
        now = datetime.now().isoformat(timespec="seconds")
        updated = 0
        topics = session.scalars(select(Topic)).all()
        for topic in topics:
            # JSON 列必须换全新容器，原地改写 flush 时新旧相等不会发 UPDATE
            evidence = dict(topic.evidence or {})
            if "base_score" not in evidence:
                evidence["base_score"] = topic.score or 0.0
            records = by_topic.get(topic.id, [])
            total = sum(engagement(r.metrics) for r in records)
            effect = effect_score(total)
            new_score = round(evidence["base_score"] + effect, 4)
            if new_score != (topic.score or 0.0):
                updated += 1
            topic.score = new_score
            evidence["score_recompute"] = {
                "publish_record_max_id": max_record_id,
                "published_records": len(records),
                "engagement_total": round(total, 2),
                "effect_score": round(effect, 4),
                "at": now,
            }
            topic.evidence = evidence
        summary = {
            "topics": len(topics),
            "updated": updated,
            "publish_records": sum(len(v) for v in by_topic.values()),
            "publish_record_max_id": max_record_id,
        }
    logger.info("topics.score 重算完成：%s", summary)
    return summary


# ---- 模板效果分（派生报表，不落字段）----
def _article_prompt_key(article: Article, prompts_by_id: dict[int, Prompt]):
    """归因键：优先 articles.prompt_id 列，历史行回退 meta.prompt_id；
    都没有 → 未知版本组（P0-P1 早期文章无版本记录，归组展示不报错）。"""
    pid = article.prompt_id or (article.meta or {}).get("prompt_id")
    prompt = prompts_by_id.get(pid) if pid is not None else None
    if prompt is None:
        return {"platform": article.platform, "scenario": "unknown", "version": "未知版本", "prompt_id": None}
    return {
        "platform": prompt.platform,
        "scenario": prompt.scenario,
        "version": f"v{prompt.version}",
        "prompt_id": prompt.id,
    }


def prompt_stats(db_path=None) -> list[dict]:
    """按 prompt 版本聚合已发布文章的互动均值（每篇文章多条回填先合并再求均值）。"""
    with session_scope(db_path) as session:
        prompts_by_id = {p.id: p for p in session.scalars(select(Prompt))}
        published = session.scalars(
            select(Article).where(Article.status == "published").order_by(Article.id)
        ).all()
        by_article: dict[int, dict[str, float]] = {}
        for record in session.scalars(select(PublishRecord)):
            m = record.metrics or {}
            slot = by_article.setdefault(
                record.article_id, {"likes": 0.0, "collects": 0.0, "comments": 0.0, "records": 0.0}
            )
            for key in ("likes", "collects", "comments"):
                slot[key] += float(m.get(key, 0) or 0)
            slot["records"] += 1

        groups: dict[tuple, dict] = {}
        for article in published:
            key_dict = _article_prompt_key(article, prompts_by_id)
            key = (key_dict["platform"], key_dict["scenario"], key_dict["version"])
            slot = by_article.get(article.id) or {"likes": 0.0, "collects": 0.0, "comments": 0.0}
            group = groups.setdefault(
                key,
                {**key_dict, "published_count": 0, "likes": 0.0, "collects": 0.0, "comments": 0.0},
            )
            group["published_count"] += 1
            for metric in ("likes", "collects", "comments"):
                group[metric] += slot[metric]

        stats = []
        for group in groups.values():
            n = group.pop("published_count")
            avg = {metric: round(group.pop(metric) / n, 2) for metric in ("likes", "collects", "comments")}
            stats.append(
                {
                    **group,
                    "published_count": n,
                    "avg_likes": avg["likes"],
                    "avg_collects": avg["collects"],
                    "avg_comments": avg["comments"],
                    # 数据量不足只展示，不给出任何启停建议
                    "sufficient_samples": n >= config.PROMPT_STATS_MIN_SAMPLES,
                }
            )
        stats.sort(key=lambda g: (g["platform"], g["scenario"], str(g["version"])))
        return stats


# ---- 成本报表（估算口径，依赖 config 单价；未按真实供应商修正前不作账单依据）----
def _usage_of(article: Article) -> dict:
    usage = (article.meta or {}).get("usage") or {}
    return {
        "model": str(usage.get("model", "")),
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "cost_est": float(usage.get("cost_est", 0.0) or 0.0),
    }


def cost_report(month: str | None = None, db_path=None) -> dict:
    """按月聚合双端 tokens / 文章数 / cost_est；直接给出 xhs 平均单篇成本。

    month 形如 YYYY-MM，缺省当月；文章归属月按 articles.created_at。
    """
    target = month or datetime.now().strftime("%Y-%m")
    with session_scope(db_path) as session:
        articles = session.scalars(select(Article).order_by(Article.id)).all()

    def _accumulate(bucket: dict, platform: str, usage: dict) -> None:
        slot = bucket.setdefault(
            platform,
            {"articles": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_est": 0.0},
        )
        slot["articles"] += 1
        slot["prompt_tokens"] += usage["prompt_tokens"]
        slot["completion_tokens"] += usage["completion_tokens"]
        slot["cost_est"] = round(slot["cost_est"] + usage["cost_est"], 6)

    month_platforms: dict[str, dict] = {}
    history: dict[str, dict[str, dict]] = {}
    month_models: set[str] = set()
    for article in articles:
        usage = _usage_of(article)
        if usage["model"] == "mock":
            continue  # mock 脚手架行零成本，混入只会稀释"平均单篇成本"
        if not (usage["model"] or usage["prompt_tokens"] or usage["completion_tokens"]):
            continue  # 无 usage 记账的行不进成本口径
        ym = article.created_at.strftime("%Y-%m") if article.created_at else "unknown"
        _accumulate(history.setdefault(ym, {}), article.platform, usage)
        if ym == target:
            _accumulate(month_platforms, article.platform, usage)
            month_models.add(usage["model"])

    def _platform_view(articles_by_platform: dict) -> dict:
        view = {}
        for platform in sorted(articles_by_platform):
            slot = articles_by_platform[platform]
            slot["cost_est"] = round(slot["cost_est"], 6)
            slot["avg_cost_est"] = round(slot["cost_est"] / slot["articles"], 6) if slot["articles"] else 0.0
            view[platform] = slot
        return view

    platforms = _platform_view(month_platforms)
    total = {
        "articles": sum(p["articles"] for p in platforms.values()),
        "prompt_tokens": sum(p["prompt_tokens"] for p in platforms.values()),
        "completion_tokens": sum(p["completion_tokens"] for p in platforms.values()),
        "cost_est": round(sum(p["cost_est"] for p in platforms.values()), 6),
    }
    history_view = []
    for ym in sorted(history, reverse=True):
        by_platform = _platform_view(history[ym])
        history_view.append(
            {
                "month": ym,
                "articles": sum(p["articles"] for p in by_platform.values()),
                "prompt_tokens": sum(p["prompt_tokens"] for p in by_platform.values()),
                "completion_tokens": sum(p["completion_tokens"] for p in by_platform.values()),
                "cost_est": round(sum(p["cost_est"] for p in by_platform.values()), 6),
                "platforms": by_platform,
            }
        )
    xhs = platforms.get("xhs")

    def _model_prices() -> list[dict]:
        # 当月出现过的模型及各自单价（多模型并存时成本归因不失真的关键）；
        # 查不到配置或没填单价的模型回退 .env 默认价并标注来源
        rows = []
        for name in sorted(month_models):
            prices = model_config.price_for(name)
            rows.append(
                {
                    "model": name,
                    "input_per_m": prices[0] if prices else config.LLM_PRICE_INPUT_PER_M,
                    "output_per_m": prices[1] if prices else config.LLM_PRICE_OUTPUT_PER_M,
                    "price_source": "model_configs" if prices else "env_default",
                }
            )
        return rows

    return {
        "month": target,
        "price": {
            "input_per_m": config.LLM_PRICE_INPUT_PER_M,
            "output_per_m": config.LLM_PRICE_OUTPUT_PER_M,
            "models": _model_prices(),
            "basis": (
                "估算口径：cost_est 按生成时各模型配置单价（/models 页）折算，"
                "未配置单价的模型回退 .env 默认价；未按供应商官方价修正前不作账单依据"
            ),
        },
        "platforms": platforms,
        "total": total,
        # 验收问句"一篇小红书笔记平均生成成本是多少"的直接答案（当月，无数据为 null）
        "xhs_avg_cost_per_article": round(xhs["avg_cost_est"], 6) if xhs else None,
        "history": history_view,
    }


def article_cost(article_id: int, db_path=None) -> dict | None:
    """单篇生成成本明细（该 article 的 meta.usage 原样展示）。"""
    with session_scope(db_path) as session:
        article = session.get(Article, article_id)
        if article is None:
            return None
        return {
            "article_id": article.id,
            "topic_id": article.topic_id,
            "platform": article.platform,
            "status": article.status,
            "created_at": article.created_at,
            "usage": _usage_of(article),
        }


# ---- 阈值校准视图（人工结论，系统不自动改参数）----
def _published_effect_by_hot_item(session) -> dict[int, dict]:
    """每个 hot_item 经 evidence 关联到的选题的实际发布效果（互动合计 + 选题分）。

    radar 建题时把样本快照写进 topics.evidence.items[].hot_item_id，以此反查。
    """
    by_topic = _records_by_topic(session)
    topics = session.scalars(select(Topic).where(Topic.source == "radar")).all()
    effect: dict[int, dict] = {}
    for topic in topics:
        items = (topic.evidence or {}).get("items") or []
        total = sum(engagement(r.metrics) for r in by_topic.get(topic.id, []))
        if total <= 0:
            continue
        for item in items:
            hid = item.get("hot_item_id") if isinstance(item, dict) else None
            if hid is not None:
                effect[hid] = {"engagement": round(total, 2), "topic_id": topic.id, "score": topic.score}
    return effect


def calibration_view(db_path=None) -> dict:
    """viral_samples 判定结果 × 实际发布效果交叉表 + 当前阈值。

    would_pass_now 按当前 config 阈值即时重判（radar 现读 config，改值即生效）；
    校准结论由周四校准会人工拍板后改环境变量并记录 docs/p4-calibration.md。
    """
    with session_scope(db_path) as session:
        # 全量拉取：summary 必须反映总体（limit 截断后"最少/均值"会失真）；
        # 展示列表另行截 200 条。样本量增长到万级时改为 SQL 聚合下推。
        rows = session.execute(radar.query_viral_samples(session)).all()
        effect = _published_effect_by_hot_item(session)

    samples = []
    for sample, item in rows:
        # 判定与爆文率按源分流：gzh 走阅读量口径，其余走低粉爆款口径
        if item.source == "gzh":
            score_fn, viral_fn = radar.gzh_viral_score, radar.is_gzh_viral
        else:
            score_fn, viral_fn = radar.viral_score, radar.is_low_fans_viral
        entry = {
            "viral_sample_id": sample.id,
            "hot_item_id": item.id,
            "source": item.source,
            "title": item.title,
            "domain": sample.domain,
            "fans": item.fans,
            "likes": item.likes,
            "collects": item.collects,
            "comments": item.comments,
            "viral_score": sample.viral_score,
            "recomputed_viral_score": score_fn(item),
            "would_pass_now": viral_fn(item),
            "published_effect": effect.get(item.id),
        }
        if item.source == "gzh":
            article = radar._gzh_article(item)
            entry.update(
                {
                    "reads": radar._gzh_metric(article, "readCount"),
                    "watches": radar._gzh_metric(article, "watchCount"),
                    "shares": radar._gzh_metric(article, "shareCount"),
                }
            )
        samples.append(entry)
    scores = [s["viral_score"] for s in samples]
    return {
        "thresholds": {
            "viral_fans_max": config.VIRAL_FANS_MAX,
            "viral_likes_min": config.VIRAL_LIKES_MIN,
            "viral_score_min": config.VIRAL_SCORE_MIN,
            "gzh_reads_min": config.GZH_READS_MIN,
            "gzh_score_min": config.GZH_SCORE_MIN,
            "topic_duplicate_jaccard": config.TOPIC_JACCARD_THRESHOLD,
            "note": (
                "修改方式：环境变量 CF_VIRAL_FANS_MAX / CF_VIRAL_LIKES_MIN / CF_VIRAL_SCORE_MIN /"
                " CF_GZH_READS_MIN / CF_GZH_SCORE_MIN / CF_TOPIC_DUPLICATE_JACCARD"
                "（改后重启进程或运行中改 config 属性即生效）；结论记录到 docs/p4-calibration.md"
            ),
        },
        "samples": samples[:200],
        "summary": {
            "sample_count": len(samples),
            "would_pass_now": sum(1 for s in samples if s["would_pass_now"]),
            "published_sample_count": sum(1 for s in samples if s["published_effect"]),
            "viral_score_min": min(scores) if scores else None,
            "viral_score_max": max(scores) if scores else None,
            "viral_score_avg": round(sum(scores) / len(scores), 4) if scores else None,
        },
    }
