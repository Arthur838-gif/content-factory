"""Collector 协议与采集入库管线（计划书 6.2）。

所有采集器实现统一协议 fetch() -> list[HotItem]；落库前按 URL 去重、
按领域关键词表过滤；命中关键词的条目交给 radar 自动生成候选选题。

P-1b：熔断状态持久化到 collector_state（连续失败 3 次熔断、只告警一次、
仅人工恢复）；xhs 条目走低粉爆款判定管线（viral_samples + 自动建题）。
"""
import logging
from abc import ABC, abstractmethod
from typing import Callable

from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import CollectorState, HotItem as HotItemORM
from ..schemas import CollectorRunResult, HotItem
from ..services import notify, radar

logger = logging.getLogger(__name__)


class UnknownCollectorError(KeyError):
    pass


class CircuitOpenError(RuntimeError):
    """采集器已熔断，拒绝执行；需人工恢复（POST /api/admin/collectors/{name}/resume）。"""


class BaseCollector(ABC):
    """采集器协议。单源失败只记日志，不影响其他源。"""

    name: str = "base"

    @abstractmethod
    def fetch(self) -> list[HotItem]:
        raise NotImplementedError


class FailureTracker:
    """热榜单源连续失败计数（进程内内存态，重启清零）。

    用于 hotboard 内部 per-source 告警；采集器整体级的失败计数与熔断
    由 collector_state 持久化（P-1b），见 record_failure / record_success。
    """

    def __init__(self, alert_after: int = config.COLLECTOR_FAIL_ALERT_AFTER):
        self.alert_after = alert_after
        self._counts: dict[str, int] = {}
        self._alerted: set[str] = set()

    def track_failure(self, key: str, detail: str = "") -> int:
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        # 达到阈值只告警一次；恢复成功后重新武装，可再次告警（防告警风暴）
        if count >= self.alert_after and key not in self._alerted:
            self._alerted.add(key)
            notify.send_alert("WARN", "collector", f"{key} 连续失败 {count} 次", detail)
        return count

    def track_success(self, key: str) -> None:
        self._counts.pop(key, None)
        self._alerted.discard(key)


failure_tracker = FailureTracker()


# ---- 熔断状态（持久化，不自愈）----
def _get_state(session, name: str) -> CollectorState:
    state = session.scalars(select(CollectorState).where(CollectorState.name == name)).first()
    if state is None:
        state = CollectorState(name=name)
        session.add(state)
        session.flush()
    return state


def circuit_open(name: str, db_path=None) -> bool:
    with session_scope(db_path) as session:
        state = session.scalars(select(CollectorState).where(CollectorState.name == name)).first()
        return state is not None and state.status == "open"


def record_failure(name: str, detail: str = "") -> bool:
    """失败计数 +1；达到阈值即熔断。返回是否“刚刚熔断”（用于只告警一次）。"""
    with session_scope() as session:
        state = _get_state(session, name)
        state.consecutive_failures = (state.consecutive_failures or 0) + 1
        state.last_error = detail[:2000]
        if state.status != "open" and state.consecutive_failures >= config.COLLECTOR_CIRCUIT_FAILURES:
            state.status = "open"
            from datetime import datetime

            state.opened_at = datetime.now()
            return True
    return False


def record_success(name: str) -> None:
    """成功即清零失败计数（未熔断时）。"""
    with session_scope() as session:
        state = _get_state(session, name)
        state.consecutive_failures = 0
        state.last_error = None


def resume_collector(name: str) -> bool:
    """人工恢复：仅熔断状态可恢复，计数清零。返回是否确实处于熔断。"""
    with session_scope() as session:
        state = session.scalars(select(CollectorState).where(CollectorState.name == name)).first()
        if state is None or state.status != "open":
            return False
        state.status = "enabled"
        state.consecutive_failures = 0
        state.last_error = None
        state.opened_at = None
        return True


def collector_status(db_path=None) -> list[dict]:
    with session_scope(db_path) as session:
        rows = session.scalars(select(CollectorState).order_by(CollectorState.name)).all()
        return [
            {
                "name": row.name,
                "status": row.status,
                "consecutive_failures": row.consecutive_failures,
                "last_error": row.last_error,
                "opened_at": row.opened_at.isoformat(timespec="seconds") if row.opened_at else None,
                "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
            }
            for row in rows
        ]


def persist_hot_items(session, items: list[HotItem], collector: str = "") -> CollectorRunResult:
    """去重 → 领域过滤 → 入库 → 自动建选题。

    热榜条目（weibo/zhihu/baidu）：命中领域即建候选选题（P-1a 行为）。
    xhs 条目：只落 hot_items；过 likes 预筛且判定为低粉爆款的才写
    viral_samples 并自动建题（P-1b）。
    """
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

    domains = radar.load_domains()  # 整批只读一次领域词表，不逐条读盘
    created = merged = filtered_out = inserted = viral_created = 0
    for url, item in batch.items():
        if url in existing:
            duplicates_skipped += 1
            continue
        matched = radar.match_domain(item.title, domains)
        if matched is None:
            filtered_out += 1
            continue
        domain, keyword = matched
        row = HotItemORM(
            source=item.source,
            title=item.title,
            url=url,
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
        if item.source == "xhs":
            outcome = radar.process_xhs_item(session, row, domain, keyword, auto=True)
            if outcome["viral"]:
                viral_created += 1
                if outcome["topic_outcome"] == "created":
                    created += 1
                else:
                    merged += 1
        elif item.source == "github":
            # P7：GitHub 项目是合集排期的真实素材（raw.keyword 命中栏目词），
            # 不单独建灵感选题——英文仓库名对 radar 撞题/评分没有意义
            pass
        else:
            outcome, _topic = radar.create_or_merge_topic(session, row, domain, keyword)
            if outcome == "created":
                created += 1
            else:
                merged += 1
        inserted += 1

    return CollectorRunResult(
        collector=collector,
        fetched=fetched,
        duplicates_skipped=duplicates_skipped,
        filtered_out=filtered_out,
        inserted=inserted,
        topics_created=created,
        topics_merged=merged,
        viral_created=viral_created,
    )


def run_collector(name: str) -> CollectorRunResult:
    """手动/定时触发入口：熔断检查 → 拉取 → 落库。整轮失败计数并熔断。"""
    collector = get_collector(name)
    if circuit_open(name):
        raise CircuitOpenError(f"采集器 {name} 已熔断，需人工恢复后才能继续执行")
    try:
        items = collector.fetch()
    except Exception as exc:  # 整个采集器异常（单源失败已在 fetch 内部消化）
        logger.exception("采集器 %s 执行失败", name)
        opened = record_failure(name, repr(exc))
        if opened:
            # 只在熔断瞬间告警一次；熔断期间不再重试、不再重复告警
            notify.send_alert(
                "ERROR", "collector", f"{name} 熔断",
                f"连续失败 {config.COLLECTOR_CIRCUIT_FAILURES} 次，采集器已暂停，"
                "需人工检查并通过 POST /api/admin/collectors/"
                f"{name}/resume 显式恢复",
            )
        raise
    record_success(name)
    with session_scope() as session:
        result = persist_hot_items(session, items, collector=name)
    logger.info(
        "采集完成 %s：fetched=%s inserted=%s dup=%s filtered=%s topics(+%s/merge %s) viral=%s",
        name, result.fetched, result.inserted, result.duplicates_skipped,
        result.filtered_out, result.topics_created, result.topics_merged, result.viral_created,
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


# ---- 手动触发的运维任务（非 fetch 协议，不参与熔断）----
# 采集器之外的可手动触发任务统一登记于此（如 xhs_teardown），路由层从
# available_tasks() 读清单，不再各自维护伪注册表；任务实现方 import 本模块
# 后调 register_manual_task 登记（避免本模块反向依赖 services 造成环）。
_MANUAL_TASKS: dict[str, Callable] = {}


def register_manual_task(name: str, fn: Callable) -> None:
    _MANUAL_TASKS[name] = fn


def get_manual_task(name: str) -> Callable | None:
    return _MANUAL_TASKS.get(name)


def available_tasks() -> list[str]:
    return sorted(_MANUAL_TASKS)
