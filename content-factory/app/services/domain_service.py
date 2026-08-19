"""领域词表服务（P-2）：数据库为唯一事实源，data/domains.yml 只作种子导入源。

- seed_domains：启动时幂等导入 YAML 种子 + 登记官方 24 类目（只读 YAML，不回写）；
- load_domains / match_domain：采集入库过滤与建题的领域匹配（radar 旧签名委托到此）；
- upsert_domain：登记/合并领域与关键词（建栏目同事务调用，取代 YAML 读改写）；
- list_domains / set_domain_enabled：领域管理 API 的数据层。

匹配语义与 YAML 时代一致：多领域命中取先声明者（ordering 小者先）；
停用（enabled=False）领域不参与匹配与采样词兜底。ordering 分两段：
自定义领域 10 起步，官方类目 1000 起步——保持"官方类目后登记、兜底匹配"
的旧行为。topics.domain 等历史字符串快照不回填、不加外键。
"""
import logging
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path

import yaml
from sqlalchemy import func, select

from .. import config
from ..collectors.redfox import XHS_CATEGORIES
from ..db import session_scope
from ..models import Domain, DomainKeyword

logger = logging.getLogger(__name__)

# 官方类目 ordering 起点（自定义领域从 10 起，官方垫后，与 YAML 时代一致）
_OFFICIAL_ORDERING_BASE = 1000
_ORDERING_STEP = 10

MAX_NAME_LENGTH = 64
MAX_KEYWORD_LENGTH = 64


def _read_seed_yaml(path: Path) -> dict[str, list[str]]:
    """读种子 YAML（两种历史写法等价）；文件不存在返回空表。"""
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    domains = data.get("domains") or {}
    result: dict[str, list[str]] = {}
    for domain, spec in domains.items():
        keywords = (spec or {}).get("keywords", []) if isinstance(spec, dict) else (spec or [])
        result[str(domain)] = [str(kw) for kw in keywords]
    return result


def _domain_type(name: str) -> str:
    return "official" if name in XHS_CATEGORIES else "custom"


def _next_domain_ordering(session, dtype: str) -> int:
    """同段（custom/official）现有最大 ordering + 步长；段内首个落在段起点后。"""
    base = _OFFICIAL_ORDERING_BASE if dtype == "official" else 0
    current = session.scalars(
        select(func.max(Domain.ordering)).where(Domain.type == dtype)
    ).first()
    return (current if current is not None else base) + _ORDERING_STEP


def upsert_domain(session, name: str, keywords: list[str], source: str = "user") -> dict:
    """登记/合并领域与关键词（在调用方事务内执行，不自行提交）。

    新领域按类型分配段内最大 ordering+10；已有领域只追加缺失关键词，
    不改既有 ordering/type（匹配优先级稳定）。返回登记结果摘要。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("领域名不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"领域名超过 {MAX_NAME_LENGTH} 字符")

    clean: list[str] = []
    for kw in keywords or []:
        kw = str(kw).strip()
        if not kw:
            continue
        if len(kw) > MAX_KEYWORD_LENGTH:
            logger.warning("关键词超长丢弃（%s…）", kw[:20])
            continue
        if kw not in clean:
            clean.append(kw)

    domain = session.scalars(select(Domain).where(Domain.name == name)).first()
    created = False
    if domain is None:
        domain = Domain(name=name, type=_domain_type(name))
        domain.ordering = _next_domain_ordering(session, domain.type)
        session.add(domain)
        session.flush()
        created = True

    existing = set(
        session.scalars(
            select(DomainKeyword.keyword).where(DomainKeyword.domain_id == domain.id)
        ).all()
    )
    next_order = (
        session.scalars(
            select(func.max(DomainKeyword.ordering)).where(DomainKeyword.domain_id == domain.id)
        ).first()
        or 0
    ) + 1
    added: list[str] = []
    for kw in clean:
        if kw in existing:
            continue
        session.add(
            DomainKeyword(domain_id=domain.id, keyword=kw, ordering=next_order, source=source)
        )
        next_order += 1
        added.append(kw)

    return {
        "name": name,
        "created": created,
        "added_keywords": added,
        "keywords_total": len(existing) + len(added),
    }


def seed_domains(session=None, yml_path: Path | None = None) -> dict:
    """幂等种子导入：YAML 领域（声明序）→ 官方 24 类目登记（无关键词）。

    只增不删不改序：YAML 后续手工增词，重启后合并进库；库里已删的词
    不会复活（upsert 只追加）。返回导入统计（含库内总量）。
    """
    yml = _read_seed_yaml(Path(yml_path or config.DOMAINS_FILE))
    own = session is None
    with (session_scope() if own else nullcontext(session)) as sess:
        domains_created = keywords_added = 0
        for name, keywords in yml.items():
            try:
                result = upsert_domain(sess, name, keywords, source="seed")
            except ValueError:
                logger.warning("种子领域非法，跳过：%r", name)
                continue
            domains_created += 1 if result["created"] else 0
            keywords_added += len(result["added_keywords"])

        officials_registered = 0
        for category in XHS_CATEGORIES:
            exists = sess.scalars(select(Domain.id).where(Domain.name == category)).first()
            if exists is None:
                sess.add(
                    Domain(
                        name=category,
                        type="official",
                        ordering=_next_domain_ordering(sess, "official"),
                    )
                )
                officials_registered += 1
        sess.flush()
        total_domains = sess.scalar(select(func.count(Domain.id))) or 0
        total_keywords = sess.scalar(select(func.count(DomainKeyword.id))) or 0
    stats = {
        "seed_domains": len(yml),
        "domains_created": domains_created,
        "keywords_added": keywords_added,
        "officials_registered": officials_registered,
        "domains_total": total_domains,
        "keywords_total": total_keywords,
    }
    if domains_created or keywords_added or officials_registered:
        logger.info("领域种子导入：%s", stats)
    return stats


def load_domains(session=None) -> dict[str, list[str]]:
    """启用领域的 {名称: [关键词]}（ordering 升序 = 匹配优先级）。

    session 传入则借用调用方事务（批量采集整轮只查一次）；
    缺省自开会话（单条入口，如人工喂样本）。
    """
    own = session is None
    with (session_scope() if own else nullcontext(session)) as sess:
        rows = sess.scalars(
            select(Domain).where(Domain.enabled).order_by(Domain.ordering, Domain.id)
        ).all()
        if not rows:
            return {}
        keyword_rows = sess.scalars(
            select(DomainKeyword)
            .where(DomainKeyword.domain_id.in_([d.id for d in rows]))
            .order_by(DomainKeyword.domain_id, DomainKeyword.ordering, DomainKeyword.id)
        ).all()
    by_domain: dict[int, list[str]] = defaultdict(list)
    for kw in keyword_rows:
        by_domain[kw.domain_id].append(kw.keyword)
    return {d.name: by_domain.get(d.id, []) for d in rows}


def match_domain(
    title: str,
    domains: dict[str, list[str]] | None = None,
    session=None,
) -> tuple[str, str] | None:
    """返回 (领域, 命中关键词)；多领域命中取先声明者；未命中返回 None。

    domains 传入预载词表（批量场景）；缺省现查库。
    """
    text = (title or "").lower()
    for domain, keywords in (domains if domains is not None else load_domains(session)).items():
        for keyword in keywords:
            if keyword.lower() in text:
                return domain, keyword
    return None


def keyword_domain(
    keyword: str,
    domains: dict[str, list[str]] | None = None,
    session=None,
) -> tuple[str, str] | None:
    """采样词精确反查 (领域, 关键词)；不在词表返回 None（ordering 优先）。

    关键词采样条目的入库过滤用：标题没命中词表时，采样词本身在词表
    （栏目词池建栏目时已登记，是策展过的检索意图）则按该词的领域放行——
    花钱定向搜回的结果不该再被标题字面匹配扔掉。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return None
    for domain, keywords in (domains if domains is not None else load_domains(session)).items():
        for k in keywords:
            if k.lower() == kw:
                return domain, k
    return None


def list_domains(session=None, include_disabled: bool = True) -> list[dict]:
    """领域清单（管理 API / 页面下拉用），ordering 升序。"""
    own = session is None
    with (session_scope() if own else nullcontext(session)) as sess:
        stmt = select(Domain).order_by(Domain.ordering, Domain.id)
        if not include_disabled:
            stmt = stmt.where(Domain.enabled)
        rows = sess.scalars(stmt).all()
        keyword_rows = sess.scalars(
            select(DomainKeyword)
            .where(DomainKeyword.domain_id.in_([d.id for d in rows]))
            .order_by(DomainKeyword.domain_id, DomainKeyword.ordering, DomainKeyword.id)
        ).all()
    by_domain: dict[int, list[str]] = defaultdict(list)
    for kw in keyword_rows:
        by_domain[kw.domain_id].append(kw.keyword)
    return [
        {
            "name": d.name,
            "type": d.type,
            "enabled": bool(d.enabled),
            "ordering": d.ordering,
            "keywords": by_domain.get(d.id, []),
            "keyword_count": len(by_domain.get(d.id, [])),
            "created_at": d.created_at.isoformat(timespec="seconds") if d.created_at else None,
            "updated_at": d.updated_at.isoformat(timespec="seconds") if d.updated_at else None,
        }
        for d in rows
    ]


def set_domain_enabled(name: str, enabled: bool) -> bool:
    """启用/停用领域（停用后不参与匹配与采样词兜底）。返回领域是否存在。"""
    with session_scope() as session:
        row = session.scalars(select(Domain).where(Domain.name == name)).first()
        if row is None:
            return False
        row.enabled = bool(enabled)
        return True
