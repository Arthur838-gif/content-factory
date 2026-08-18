"""M1 热榜采集器：RSSHub（微博 / 知乎 / 百度）。

不做全文抓取，只要标题、链接、热度字段；不登录、不带 Cookie。
单源失败只记日志并计入连续失败，不影响其他源。
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .. import config
from ..schemas import HotItem
from .base import BaseCollector, failure_tracker, register_collector

logger = logging.getLogger(__name__)

# 进程内共享连接（本模块仅同步低频调用；免得每源每轮新建 TCP 连接）
_http = httpx.Client(
    timeout=config.HTTP_TIMEOUT_SECONDS,
    headers={"User-Agent": "content-factory/0.1"},
)


def _local_naive(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_rss(xml_text: str, source: str, author: str) -> list[HotItem]:
    """RSS 2.0 → HotItem 列表。rank（榜单位次）记入 raw。

    解析前做体积与 DTD/实体拒绝：xml.etree 无实体展开防护（billion-laughs 风险），
    正常 RSSHub 输出既不带 DOCTYPE 也不会超兆级体积。
    """
    if len(xml_text) > config.RSS_MAX_XML_CHARS:
        raise ValueError(f"RSS 报文超限（{len(xml_text)} > {config.RSS_MAX_XML_CHARS} 字符）")
    lowered = xml_text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("RSS 报文含 DTD/实体声明，拒绝解析")
    root = ET.fromstring(xml_text)
    items: list[HotItem] = []
    rank = 0
    for element in root.iter():
        if _local_name(element.tag) != "item":
            continue
        rank += 1
        title = _child_text(element, "title")
        if not title:
            continue
        items.append(
            HotItem(
                source=source,
                title=title,
                url=_child_text(element, "link"),
                author=author,
                captured_at=_local_naive(_child_text(element, "pubDate")),
                raw={
                    "rank": rank,
                    "description": _child_text(element, "description")[:500],
                    "board": author,
                },
            )
        )
    return items


@register_collector
class HotboardCollector(BaseCollector):
    name = "hotboard"

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        failures = 0
        for source_name, spec in config.HOTBOARD_SOURCES.items():
            key = f"{self.name}:{source_name}"
            try:
                xml_text = self._fetch_source(source_name, spec["route"])
                parsed = parse_rss(xml_text, source=source_name, author=spec["label"])
                if not parsed:
                    raise ValueError("RSS 解析结果为空")
                items.extend(parsed)
                failure_tracker.track_success(key)
                logger.info("热榜源 %s 拉取 %s 条", source_name, len(parsed))
            except Exception as exc:
                # 单源失败只记日志不影响其他源；连续失败由 tracker 外发告警
                failures += 1
                logger.warning("热榜源 %s 拉取失败：%r", source_name, exc)
                failure_tracker.track_failure(key, f"{source_name}: {exc!r}")
        if failures and failures == len(config.HOTBOARD_SOURCES):
            # 全部源失败 = 采集器整体不可用（如 RSSHub 实例挂了）。
            # 吞掉异常会让 run_collector 记 success，熔断器永远不触发。
            raise RuntimeError(f"热榜全部 {failures} 个源拉取失败")
        return items

    def _fetch_source(self, source_name: str, route: str) -> str:
        if config.RSSHUB_BASE_URL.startswith("file://"):
            fixture = Path(config.RSSHUB_BASE_URL[len("file://"):]) / f"{source_name}.xml"
            return fixture.read_text(encoding="utf-8")
        resp = _http.get(config.RSSHUB_BASE_URL + route)
        resp.raise_for_status()
        return resp.text
