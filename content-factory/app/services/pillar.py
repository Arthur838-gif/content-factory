"""内容栏目（pillar）周排期规划器（P5）。

栏目 = 可持续系列（固定名称 + 固定角度结构 + 每周期数 + 专属采样关键词池），
解决一次性选题无法支撑账号日更/周更的问题。

plan_week 按栏目周期从当周采样素材（hot_items）派生选题：
- slots_per_week = 1：周更固定档（如「本周5个值得装的AI工具（08.18–08.24）」），
  素材取当周互动最高的 N 条快照进 evidence，标题含周期区间故天然幂等；
- slots_per_week > 1：多期轮换档，每期绑定一条素材（标题 = 栏目名 + 素材标题），
  已用过的素材（URL 在本周 pillar 选题 evidence 里）不重复排期。

pillar 选题（topics.source='pillar'）不参与 radar 撞题合并——系列内容标题
本来就高度相似，Jaccard 合并会把「第 13 期」并进「第 12 期」。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import desc, select

from ..db import session_scope
from ..models import HotItem, Pillar, Topic
from . import radar

logger = logging.getLogger(__name__)

# 周更固定档 evidence 里携带的素材条数（喂 reference_points 供生成引用）
COLLECTION_EVIDENCE_SIZE = 5


def week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """本周一 00:00 ~ 周日 23:59:59（本地时区，与调度约定一致）。"""
    now = now or datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start, week_start + timedelta(days=7) - timedelta(seconds=1)


def active_pillars(session) -> list[Pillar]:
    return list(
        session.scalars(select(Pillar).where(Pillar.active).order_by(Pillar.id)).all()
    )


def pillar_keywords(session) -> list[str]:
    """启用栏目的关键词池（去重保序）——xhs_sample 采样词优先取这里。"""
    keywords: list[str] = []
    for pillar in active_pillars(session):
        for kw in pillar.keywords or []:
            if kw and kw not in keywords:
                keywords.append(kw)
    return keywords


def _match_keyword(item: HotItem, keywords: list[str]) -> str | None:
    """标题 / 正文 desc 字面命中，或该条由栏目关键词采样回来（raw.keyword）。"""
    raw = item.raw or {}
    texts = [item.title.lower()]
    article = raw.get("article")
    if isinstance(article, dict) and isinstance(article.get("desc"), str):
        texts.append(article["desc"].lower())
    for kw in keywords:
        if kw and any(kw.lower() in text for text in texts):
            return kw
    sampled = raw.get("keyword")
    if isinstance(sampled, str) and sampled in keywords:
        return sampled
    return None


def _week_pillar_topics(session, week_start: datetime) -> list[Topic]:
    rows = session.scalars(
        select(Topic).where(Topic.source == "pillar", Topic.created_at >= week_start)
    ).all()
    return list(rows)


def _snapshot(item: HotItem, keyword: str) -> dict:
    # 领域取 radar 词表口径（标签候选/领域列展示一致）；未命中给空串
    matched = radar.match_domain(item.title)
    domain = matched[0] if matched else ""
    return radar._evidence_snapshot(item, domain, keyword)


def plan_pillar_week(session, pillar: Pillar, now: datetime | None = None) -> dict:
    """为单个栏目生成本周选题（幂等：重复调用只补缺口，不重复建）。

    返回 {pillar, created: [topic...], existing: 本周已有期数}。
    """
    now = now or datetime.now()
    week_start, week_end = week_bounds(now)
    week_topics = _week_pillar_topics(session, week_start)
    existing = [t for t in week_topics if (t.evidence or {}).get("pillar_id") == pillar.id]
    # 素材去重只在同栏目内生效：合集档引用过的素材，深挖档仍可单独立题
    # （合集是"参考清单"，深挖是"单独立题"，编辑上复用同一素材是合理的）
    used_urls = {
        (it or {}).get("url")
        for t in existing
        for it in (t.evidence or {}).get("items", [])
    }

    keywords = list(pillar.keywords or [])
    pool = session.scalars(
        select(HotItem)
        .where(HotItem.captured_at >= week_start, HotItem.source == "xhs")
        .order_by(desc(HotItem.likes), desc(HotItem.id))
    ).all()
    matched = [(item, kw) for item in pool if (kw := _match_keyword(item, keywords))]

    created: list[Topic] = []
    slots = max(int(pillar.slots_per_week or 1), 1)
    if slots == 1:
        # 周更固定档：一期合集，标题带周期区间（幂等键）
        if existing:
            return {"pillar": pillar.name, "created": [], "existing": len(existing)}
        title = "{}（{}–{}）".format(
            pillar.name, week_start.strftime("%m.%d"), week_end.strftime("%m.%d")
        )
        evidence = {
            "pillar_id": pillar.id,
            "pillar_name": pillar.name,
            "week_start": week_start.isoformat(timespec="seconds"),
            "items": [_snapshot(item, kw) for item, kw in matched[:COLLECTION_EVIDENCE_SIZE]],
        }
        topic = Topic(
            title=title[:512],
            angle=pillar.angle or "",
            domain=pillar.domain or "",
            source="pillar",
            status="new",
            score=radar.radar_score(matched[0][0]) if matched else 1.0,
            evidence=evidence,
            expires_at=week_end,
        )
        session.add(topic)
        session.flush()  # 拿 id 供 API 返回
        created.append(topic)
    else:
        # 多期轮换档：每期绑一条素材，补满本周缺口
        need = slots - len(existing)
        for item, kw in matched:
            if need <= 0:
                break
            if not item.title.strip() or item.url in used_urls:
                continue
            topic = Topic(
                title=f"{pillar.name}｜{item.title}"[:512],
                angle=pillar.angle or "",
                domain=pillar.domain or "",
                source="pillar",
                status="new",
                score=radar.radar_score(item),
                evidence={
                    "pillar_id": pillar.id,
                    "pillar_name": pillar.name,
                    "week_start": week_start.isoformat(timespec="seconds"),
                    "items": [_snapshot(item, kw)],
                },
                expires_at=week_end,
            )
            session.add(topic)
            session.flush()  # 拿 id 供 API 返回
            created.append(topic)
            used_urls.add(item.url)
            need -= 1
    return {"pillar": pillar.name, "created": created, "existing": len(existing)}


def plan_week(pillar_id: int | None = None, now: datetime | None = None) -> list[dict]:
    """排期入口（API / 手动触发调用）：全部启用栏目或指定栏目。"""
    with session_scope() as session:
        if pillar_id is not None:
            pillar = session.get(Pillar, pillar_id)
            if pillar is None:
                raise ValueError(f"栏目 {pillar_id} 不存在")
            pillars = [pillar]
        else:
            pillars = active_pillars(session)
        results = []
        for pillar in pillars:
            if not pillar.active and pillar_id is None:
                continue
            result = plan_pillar_week(session, pillar, now=now)
            results.append(
                {
                    "pillar": result["pillar"],
                    "created": [
                        {"id": t.id, "title": t.title} for t in result["created"]
                    ],
                    "existing": result["existing"],
                }
            )
        return results
