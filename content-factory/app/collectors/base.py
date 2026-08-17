"""Collector 协议与采集入库管线（计划书 6.2）。

所有采集器实现统一协议 fetch() -> list[HotItem]；落库前按 URL 去重、
按领域关键词表过滤；命中关键词的条目交给 radar 自动生成候选选题。
"""
import logging
from abc import ABC, abstractmethod

from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import HotItem as HotItemORM
from ..schemas import CollectorRunResult, HotItem
from ..services import notify, radar

logger = logging.getLogger(__name__)


class UnknownCollectorError(KeyError):
    pass


class BaseCollector(ABC):
    """采集器协议。单源失败只记日志，不影响其他源。"""

    name: str = "base"

    @abstractmethod
    def fetch(self) -> list[HotItem]:
        raise NotImplementedError


class FailureTracker:
    """连续失败计数（进程内内存态，重启清零）。

    达到阈值时外发一次告警（第 7 章事件①：任一采集器熔断或连续失败），
    之后每次失败续发；成功即清零。TODO(confirm)：M2 引入持久化熔断状态。
    """

    def __init__(self, alert_after: int = config.COLLECTOR_FAIL_ALERT_AFTER):
        self.alert_after = alert_after
        self._counts: dict[str, int] = {}

    def track_failure(self, key: str, detail: str = "") -> int:
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count >= self.alert_after:
            notify.send_alert("WARN", "collector", f"{key} 连续失败 {count} 次", detail)
        return count

    def track_success(self, key: str) -> None:
        self._counts.pop(key, None)


failure_tracker = FailureTracker()


def persist_hot_items(session, items: list[HotItem]) -> CollectorRunResult:
    """去重 → 领域过滤 → 入库 → 自动建选题。只处理命中领域关键词的条目。"""
    fetched = len(items)

    # 批内去重（同 URL 只留第一条），再与库内已有 URL 比对
    batch: dict[str, HotItem] = {}
    for item in items:
        batch.setdefault(item.url, item)
    duplicates_skipped = fetched - len(batch)

    existing = set(
        session.scalars(
            select(HotItemORM.url).where(HotItemORM.url.in_(list(batch.keys())))
        ).all()
    )

    created = merged = filtered_out = inserted = 0
    for url, item in batch.items():
        if url in existing:
            duplicates_skipped += 1
            continue
        matched = radar.match_domain(item.title)
        if matched is None:
            filtered_out += 1
            continue
        domain, keyword = matched
        row = HotItemORM(
            source=item.source,
            title=item.title,
            url=item.url,
            author=item.author,
            fans=item.fans,
            likes=item.likes,
            collects=item.collects,
            comments=item.comments,
            raw=item.raw,
            captured_at=item.captured_at,
        )
        session.add(row)
        session.flush()  # 拿 id 供 evidence 快照引用
        outcome, _topic = radar.create_or_merge_topic(session, row, domain, keyword)
        if outcome == "created":
            created += 1
        else:
            merged += 1
        inserted += 1

    return CollectorRunResult(
        collector="",
        fetched=fetched,
        duplicates_skipped=duplicates_skipped,
        filtered_out=filtered_out,
        inserted=inserted,
        topics_created=created,
        topics_merged=merged,
    )


def run_collector(name: str) -> CollectorRunResult:
    """手动/定时触发入口：拉取 → 落库。整轮失败计数并告警。"""
    collector = get_collector(name)
    try:
        items = collector.fetch()
    except Exception as exc:  # 整个采集器异常（单源失败已在 fetch 内部消化）
        logger.exception("采集器 %s 执行失败", name)
        failure_tracker.track_failure(name, repr(exc))
        raise
    failure_tracker.track_success(name)
    with session_scope() as session:
        result = persist_hot_items(session, items)
    result.collector = name
    logger.info(
        "采集完成 %s：fetched=%s inserted=%s dup=%s filtered=%s topics(+%s/merge %s)",
        name, result.fetched, result.inserted, result.duplicates_skipped,
        result.filtered_out, result.topics_created, result.topics_merged,
    )
    return result


_REGISTRY: dict[str, type[BaseCollector]] = {}


def register_collector(cls: type[BaseCollector]) -> type[BaseCollector]:
    _REGISTRY[cls.name] = cls
    return cls


def get_collector(name: str) -> BaseCollector:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise UnknownCollectorError(name) from None


def available_collectors() -> list[str]:
    return sorted(_REGISTRY)
