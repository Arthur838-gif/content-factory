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
from ..models import HotItem, Pillar, Topic, WeekTheme
from ..schemas import WeekThemePlan
from . import generator, radar

logger = logging.getLogger(__name__)

# 周更固定档 evidence 里携带的素材条数（喂 reference_points 供生成引用）
COLLECTION_EVIDENCE_SIZE = 5
# 合集档 / 主题规划的最低素材门槛：低于此数建题等于让模型编内容（P5b 防虚构）
COLLECTION_MIN_MATERIALS = 3


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


def _matched_pool(session, pillar: Pillar, week_start: datetime) -> list[tuple[HotItem, str]]:
    """本周命中栏目关键词的采样素材（点赞倒序），主题规划与排期共用。"""
    keywords = list(pillar.keywords or [])
    pool = session.scalars(
        select(HotItem)
        .where(HotItem.captured_at >= week_start, HotItem.source == "xhs")
        .order_by(desc(HotItem.likes), desc(HotItem.id))
    ).all()
    return [(item, kw) for item in pool if (kw := _match_keyword(item, keywords))]


# ---- P5b 周主题：深挖栏目每周先定主题再分期 ----

def get_week_theme(session, pillar_id: int, week_start: datetime) -> WeekTheme | None:
    """取栏目在指定周界的主题行（不分状态）。"""
    return session.scalars(
        select(WeekTheme).where(
            WeekTheme.pillar_id == pillar_id, WeekTheme.week_start == week_start
        )
    ).first()


def confirmed_week_theme(session, pillar_id: int, week_start: datetime) -> WeekTheme | None:
    row = get_week_theme(session, pillar_id, week_start)
    return row if row and row.status == "confirmed" else None


def generate_week_theme(session, pillar: Pillar, now: datetime | None = None) -> WeekTheme:
    """从本周素材归纳周主题 + 子话题分期（LLM，产物 status=proposed 待确认）。

    素材不足门槛时抛 ValueError（调用方提示先补采样）——素材太少时
    "规划主题"和"合集凑数"一样是在让模型编内容。
    """
    now = now or datetime.now()
    week_start, _ = week_bounds(now)
    matched = _matched_pool(session, pillar, week_start)
    if len(matched) < COLLECTION_MIN_MATERIALS:
        raise ValueError(
            f"本周命中素材仅 {len(matched)} 条（需 ≥{COLLECTION_MIN_MATERIALS}），请先补采样再规划主题"
        )
    slots = max(int(pillar.slots_per_week or 1), 1)
    digest = [
        {"id": item.id, "title": item.title, "likes": item.likes, "keyword": kw}
        for item, kw in matched[:30]
    ]
    system_msg = (
        "# system\n你是内容策划，为一个小红书系列栏目规划本周主题。"
        "主题要聚焦（一周内所有期都围绕它展开）、有信息增量；"
        "子话题之间互补不重叠，合起来覆盖主题，各自独立成篇。"
        "所有子话题必须基于给定素材的真实信息点，不要编造素材里没有的方向。"
    )
    user_msg = (
        "# user\n"
        f"栏目名：{pillar.name}\n"
        f"栏目固定角度：{pillar.angle or '（无）'}\n"
        f"本周需要规划子话题数：{slots}\n"
        "本周采样素材（id｜标题｜点赞｜命中关键词）：\n"
        + "\n".join(f"- {d['id']}｜{d['title']}｜赞{d['likes']}｜{d['keyword']}" for d in digest)
        + "\n\n请严格按以下 JSON 格式输出，不要输出任何其他内容：\n"
        "{\n"
        '  "theme": "本周主题，10-20 字",\n'
        '  "subtopics": [{"title": "子话题标题（不带栏目名）", "hot_item_ids": [支撑素材的 id]}]\n'
        "}\n"
        f"subtopics 恰好 {slots} 个，每个的 hot_item_ids 从上面素材 id 里选，可为空列表。"
    )
    # 规划产物非可发布文案，引用素材原文可能带平台敏感词，与周度拆解同口径豁免
    result = generator.generate("xhs", WeekThemePlan, system_msg, user_msg, check_sensitive=False)
    if result.article is None:
        raise RuntimeError(f"周主题规划失败：{result.error}")

    row = get_week_theme(session, pillar.id, week_start)
    if row is None:
        row = WeekTheme(pillar_id=pillar.id, week_start=week_start)
        session.add(row)
    row.theme = result.article.theme[:128]
    row.subtopics = [
        {"title": s.title[:128], "hot_item_ids": list(s.hot_item_ids)}
        for s in result.article.subtopics[:slots]
    ]
    row.status = "proposed"
    session.flush()
    return row


def confirm_week_theme(
    session, pillar: Pillar, theme: str | None = None, subtopics: list | None = None
) -> WeekTheme:
    """确认本周主题（可顺带改写主题/子话题文案）；没有建议行时先要求生成。"""
    week_start, _ = week_bounds()
    row = get_week_theme(session, pillar.id, week_start)
    if row is None:
        raise ValueError("本周还没有主题建议，请先生成")
    if theme is not None and theme.strip():
        row.theme = theme.strip()[:128]
    if subtopics:
        row.subtopics = [
            {"title": str(s.get("title", ""))[:128], "hot_item_ids": list(s.get("hot_item_ids") or [])}
            for s in subtopics
            if isinstance(s, dict) and s.get("title")
        ]
    row.status = "confirmed"
    session.flush()
    return row


def plan_pillar_week(
    session, pillar: Pillar, now: datetime | None = None, replan_theme: bool = False
) -> dict:
    """为单个栏目生成本周选题（幂等：重复调用只补缺口，不重复建）。

    返回 {pillar, created: [topic...], existing: 本周已有期数, warning?}。
    P5b：深挖档优先按已确认的周主题分期（期数编号 + 子话题互补）；
    合集档素材不足门槛时不建题（防模型虚构内容），返回 warning。
    replan_theme=True 且本周已确认主题时，先归档未按主题排期的旧选题
    （evidence 无 subtopic 标记）再重排——"按主题重排"一键动作。
    """
    now = now or datetime.now()
    week_start, week_end = week_bounds(now)
    week_topics = _week_pillar_topics(session, week_start)
    existing = [t for t in week_topics if (t.evidence or {}).get("pillar_id") == pillar.id]
    theme_row = confirmed_week_theme(session, pillar.id, week_start)

    archived_legacy = 0
    if replan_theme and theme_row is not None:
        # 只归档"没按主题排"的旧选题（无 subtopic 标记）；已按主题的期保留
        for t in existing:
            if not (t.evidence or {}).get("subtopic"):
                t.status = "archived"
                archived_legacy += 1
        existing = [t for t in existing if t.status != "archived"]
    # 素材去重只在同栏目内生效：合集档引用过的素材，深挖档仍可单独立题
    # （合集是"参考清单"，深挖是"单独立题"，编辑上复用同一素材是合理的）
    used_urls = {
        (it or {}).get("url")
        for t in existing
        for it in (t.evidence or {}).get("items", [])
    }

    matched = _matched_pool(session, pillar, week_start)
    by_id = {item.id: (item, kw) for item, kw in matched}
    week_theme = theme_row.theme if theme_row else ""

    created: list[Topic] = []
    slots = max(int(pillar.slots_per_week or 1), 1)
    if slots == 1:
        # 周更固定档：一期合集，标题带周期区间（幂等键）
        if existing:
            return {"pillar": pillar.name, "created": [], "existing": len(existing)}
        if len(matched) < COLLECTION_MIN_MATERIALS:
            return {
                "pillar": pillar.name,
                "created": [],
                "existing": 0,
                "warning": (
                    f"本周命中素材仅 {len(matched)} 条（需 ≥{COLLECTION_MIN_MATERIALS}），"
                    "合集不建题以免模型虚构内容；请先在素材采样页补一轮采样"
                ),
            }
        title = "{}（{}–{}）".format(
            pillar.name, week_start.strftime("%m.%d"), week_end.strftime("%m.%d")
        )
        evidence = {
            "pillar_id": pillar.id,
            "pillar_name": pillar.name,
            "week_start": week_start.isoformat(timespec="seconds"),
            "week_theme": week_theme,
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
    elif theme_row is not None:
        # P5b 主题模式：按确认的子话题分期，期数连续、子话题互不重复
        covered = {(t.evidence or {}).get("subtopic") for t in existing}
        episode = len(existing)
        for sub in theme_row.subtopics or []:
            if episode >= slots:
                break
            sub_title = str(sub.get("title", "")).strip()
            if not sub_title or sub_title in covered:
                continue
            episode += 1
            items = []
            first_pair = None
            for hid in sub.get("hot_item_ids") or []:
                pair = by_id.get(int(hid)) if str(hid).isdigit() else None
                if pair:
                    items.append(_snapshot(pair[0], pair[1]))
                    first_pair = first_pair or pair
            if not items and matched:
                # 子话题没绑到有效素材：退回该子话题序号对应的最高赞素材
                first_pair = matched[min(episode - 1, len(matched) - 1)]
                items = [_snapshot(first_pair[0], first_pair[1])]
            topic = Topic(
                title=f"{pillar.name}第{episode}期｜{sub_title}"[:512],
                angle=pillar.angle or "",
                domain=pillar.domain or "",
                source="pillar",
                status="new",
                score=radar.radar_score(first_pair[0]) if first_pair else 1.0,
                evidence={
                    "pillar_id": pillar.id,
                    "pillar_name": pillar.name,
                    "week_start": week_start.isoformat(timespec="seconds"),
                    "week_theme": week_theme,
                    "episode": episode,
                    "episodes_total": slots,
                    "subtopic": sub_title,
                    "items": items,
                },
                expires_at=week_end,
            )
            session.add(topic)
            session.flush()
            created.append(topic)
    else:
        # 旧模式（未确认主题）：每期绑一条素材，补满本周缺口；仍带期数供系列上下文
        need = slots - len(existing)
        for item, kw in matched:
            if need <= 0:
                break
            if not item.title.strip() or item.url in used_urls:
                continue
            episode = slots - need + 1
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
                    "week_theme": week_theme,
                    "episode": episode,
                    "episodes_total": slots,
                    "items": [_snapshot(item, kw)],
                },
                expires_at=week_end,
            )
            session.add(topic)
            session.flush()  # 拿 id 供 API 返回
            created.append(topic)
            used_urls.add(item.url)
            need -= 1
    result = {"pillar": pillar.name, "created": created, "existing": len(existing)}
    if archived_legacy:
        result["archived_legacy"] = archived_legacy
    if (
        theme_row is not None and not replan_theme and slots > 1
        and any(not (t.evidence or {}).get("subtopic") for t in existing)
    ):
        result["warning"] = (
            "本周已确认主题，但排期里仍是未按主题的旧选题；"
            "点「按主题重排」归档旧选题并按主题重新分期"
        )
    return result


def plan_week(
    pillar_id: int | None = None,
    now: datetime | None = None,
    replan_theme: bool = False,
) -> list[dict]:
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
            result = plan_pillar_week(session, pillar, now=now, replan_theme=replan_theme)
            results.append(
                {
                    "pillar": result["pillar"],
                    "created": [
                        {"id": t.id, "title": t.title} for t in result["created"]
                    ],
                    "existing": result["existing"],
                    **({"archived_legacy": result["archived_legacy"]}
                       if result.get("archived_legacy") else {}),
                    **({"warning": result["warning"]} if result.get("warning") else {}),
                }
            )
        return results
