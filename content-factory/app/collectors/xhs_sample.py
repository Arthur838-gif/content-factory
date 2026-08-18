"""M2 小红书采样器（P-1b）：双源只读采样。

数据源优先级：
1. RedFox 爆款洞察（app.collectors.redfox，需 REDFOX_API_KEY，按调用计费）：
   搜索结果自带 authorFans，低粉爆款判定可直接跑通。
2. xiaohongshu-mcp（本地 Docker，数据小号登录）：无 Key 或 RedFox 调用
   失败时降级。其 search_feeds 不含 fans（docs/p-1b-fans-probe.md），
   条目仍落 hot_items 笔记级数据，但低粉爆款判定跳过（fans 未知不伪造），
   由人工喂样本（POST /api/viral-samples/manual）补齐 fans 后进入同一管线。

纪律（任务契约）：
- 只调用只读查询；禁止任何写接口、互动接口与账号行为
  （关注、点赞、评论、私信、回关、发布一律不碰）。
- 数据小号凭据只进本地 mcp 服务，本仓库不存 Cookie / 二维码 / 账号信息。
"""
import json
import logging

import httpx

from .. import config
from ..schemas import HotItem
from ..services import radar
from .base import BaseCollector, register_collector
from .redfox import RedFoxError, _to_int, enabled as redfox_enabled, probe as redfox_probe, search_hot_items

logger = logging.getLogger(__name__)

# 搜索结果里可能承载粉丝数的键（探针确认实际字段后收敛）
_FANS_KEYS = ("fans", "fans_count", "fan_count", "follower_count", "followers")


class McpHttpError(RuntimeError):
    """xiaohongshu-mcp 请求失败（网络 / JSON-RPC 错误 / 工具报错）。"""


def _decode_messages(resp: httpx.Response) -> list[dict]:
    """streamable-http 响应解码：JSON 或 SSE（data: 行）。"""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        messages = []
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                messages.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
        return messages
    try:
        return [resp.json()]
    except ValueError:
        return []


def call_mcp_tool(
    tool: str,
    arguments: dict,
    base_url: str | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """调 xiaohongshu-mcp 的 tools/call（streamable-http，端点 /mcp）。

    initialize 握手 → notifications/initialized → tools/call；
    工具返回的文本内容按 JSON 解析为 dict 列表。任何一步失败抛 McpHttpError，
    由 base.run_collector 计入连续失败与熔断。
    """
    base = (base_url or config.XHS_MCP_BASE_URL).rstrip("/")
    url = f"{base}/mcp"
    headers = {"Accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=timeout or config.XHS_MCP_TIMEOUT_SECONDS) as client:
        resp = client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "content-factory", "version": "0.1"},
                },
            },
            headers=headers,
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id", "")
        if session_id:
            headers["mcp-session-id"] = session_id
            client.post(
                url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )
        resp = client.post(
            url,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": tool, "arguments": arguments}},
            headers=headers,
        )
        resp.raise_for_status()
        messages = _decode_messages(resp)

    for message in messages:
        if message.get("id") != 2:
            continue
        if "error" in message:
            raise McpHttpError(f"mcp 工具 {tool} 返回错误：{message['error']}")
        result = message.get("result") or {}
        if result.get("isError"):
            raise McpHttpError(f"mcp 工具 {tool} 执行失败：{result}")
        for content in result.get("content") or []:
            if content.get("type") != "text":
                continue
            try:
                data = json.loads(content.get("text", ""))
            except json.JSONDecodeError:
                continue
            return data if isinstance(data, list) else [data]
    raise McpHttpError(f"mcp 工具 {tool} 未返回可解析内容")


def extract_fans(note: dict) -> int:
    """探针核心：在笔记 / 作者对象里找粉丝数字段，找不到返回 0（=未知，不伪造）。"""
    candidates = [note]
    for key in ("user", "author", "author_info", "interact_info"):
        value = note.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for source in candidates:
        for key in _FANS_KEYS:
            if source.get(key) is not None:
                return _to_int(source.get(key))
    return 0


def _normalize_note(note: dict) -> dict:
    """把两种搜索响应形状归一成扁平视图。

    实测（v2.5.0，2026-08-18）search_feeds 返回 {feeds:[{id, xsecToken,
    noteCard:{displayTitle, user:{userId,nickname}, interactInfo:{likedCount,
    collectedCount, commentCount}}}]}；早期/录制响应是扁平键（note_id/title/
    user.fans/liked_count…）。归一后下游字段抽取只有一套键。
    """
    if "noteCard" not in note:
        return note
    card = note.get("noteCard") or {}
    user = card.get("user") or {}
    interact = card.get("interactInfo") or {}
    return {
        "note_id": note.get("id") or note.get("note_id"),
        "title": card.get("displayTitle") or card.get("title"),
        "user": user,
        "liked_count": interact.get("likedCount"),
        "collected_count": interact.get("collectedCount"),
        "comment_count": interact.get("commentCount"),
        "xsec_token": note.get("xsecToken"),
        "note": note,
    }


def parse_search_notes(notes: list) -> list[HotItem]:
    """mcp 搜索结果 → HotItem 列表（无标题的条目跳过）。

    入参可能是 [note, ...]，也可能是 [{feeds:[note,...]}] 包装（search_feeds
    的真实返回）；两者都展开。URL 缺省按 note_id 构造 explore 链接。
    """
    flat: list[dict] = []
    for entry in notes:
        if isinstance(entry, dict) and isinstance(entry.get("feeds"), list):
            flat.extend(e for e in entry["feeds"] if isinstance(e, dict))
        elif isinstance(entry, dict):
            flat.append(entry)

    items: list[HotItem] = []
    for raw in flat:
        note = _normalize_note(raw)
        title = str(note.get("title") or "").strip()
        if not title:
            continue
        user = note.get("user") if isinstance(note.get("user"), dict) else {}
        note_id = str(note.get("note_id") or note.get("id") or "")
        url = (
            str(note.get("note_url") or note.get("url") or "")
            or (f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "")
        )
        items.append(
            HotItem(
                source="xhs",
                title=title,
                url=url,
                author=str(user.get("nickname") or note.get("nickname") or "") or None,
                fans=extract_fans(note),
                likes=_to_int(note.get("liked_count") or note.get("likes")),
                collects=_to_int(note.get("collected_count") or note.get("collects")),
                comments=_to_int(note.get("comment_count") or note.get("comments")),
                raw={"note": note},
            )
        )
    return items


def probe_fans(keyword: str = "AI工具") -> dict:
    """第 0 步 fans 字段探针（只读）。结论记录进 docs/p-1b-fans-probe.md。

    双源各测各的：probe 测 RedFox（若配了 Key），probe-mcp 测 mcp。
    用法：python -m app.collectors.xhs_sample probe [keyword] | probe-mcp [keyword]
    """
    if redfox_enabled():
        try:
            return redfox_probe(keyword)
        except RedFoxError as exc:
            return {
                "source": "redfox",
                "keyword": keyword,
                "error": str(exc),
                "conclusion": "RedFox 调用失败：检查 Key 有效性 / 余额 / 网络后重试；mcp 可用 probe-mcp 单独测",
            }
    return _probe_mcp(keyword)


def _probe_mcp(keyword: str) -> dict:
    notes = call_mcp_tool("search_feeds", {"keyword": keyword})
    parsed = parse_search_notes(notes)
    fans_values = [item.fans for item in parsed]
    return {
        "source": "mcp",
        "keyword": keyword,
        "note_count": len(parsed),
        "fans_available": any(fans > 0 for fans in fans_values),
        "fans_found": sum(1 for fans in fans_values if fans > 0),
        "conclusion": (
            "搜索结果含作者 fans 字段：自动采样可进入低粉爆款计算"
            if any(fans > 0 for fans in fans_values)
            else "搜索结果不含 fans：自动采样只落笔记级数据，低粉爆款走人工喂样本降级模式"
        ),
    }


@register_collector
class XhsSampleCollector(BaseCollector):
    """M2 小红书采样器：逐关键词采样，聚合为 HotItem 列表。

    单关键词内 RedFox 失败降级 mcp；两个源都失败才让异常冒泡计入熔断。
    URL 去重与领域过滤在 base.persist_hot_items 统一做；本类只负责拉取。
    """

    name = "xhs_sample"

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        for keyword in self._queries():
            parsed, source = self._fetch_keyword(keyword)
            # 记录命中该条的检索词：pillar 排期按标题或采样词匹配（标题未必含关键词）
            for item in parsed:
                raw = dict(item.raw or {})
                raw.setdefault("keyword", keyword)
                item.raw = raw
            logger.info("xhs 采样 %s（%s）：%s 条笔记", keyword, source, len(parsed))
            items.extend(parsed)
        return items

    def _fetch_keyword(self, keyword: str) -> tuple[list[HotItem], str]:
        """单关键词采样：RedFox 优先（含 fans），失败降级 mcp search_feeds。"""
        if redfox_enabled():
            try:
                return search_hot_items(keyword), "redfox"
            except RedFoxError as exc:
                logger.warning("redfox 采样 %s 失败，降级 mcp：%s", keyword, exc)
        # 只读搜索工具（v2.5.0 实测名为 search_feeds；早期文档写作 search_notes）；
        # 写/互动类工具一律不调用（任务契约 1）
        return parse_search_notes(self._search(keyword)), "mcp"

    def _search(self, keyword: str) -> list[dict]:
        return call_mcp_tool("search_feeds", {"keyword": keyword})

    def _queries(self) -> list[str]:
        # 优先级：环境变量显式指定 > 启用栏目的关键词池（P5，栏目驱动采样）> 领域词表
        if config.XHS_SAMPLE_KEYWORDS:
            return list(config.XHS_SAMPLE_KEYWORDS)
        from ..db import session_scope
        from ..services import pillar as pillar_service

        with session_scope() as session:
            keywords = pillar_service.pillar_keywords(session)
        if keywords:
            return keywords[: config.XHS_SAMPLE_MAX_QUERIES]
        keywords = []
        for domain_keywords in radar.load_domains().values():
            keywords.extend(domain_keywords)
        return keywords[: config.XHS_SAMPLE_MAX_QUERIES]


def main(argv: list[str]) -> int:
    if argv and argv[0] == "probe":
        print(json.dumps(probe_fans(*argv[1:2]), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "probe-mcp":
        print(json.dumps(_probe_mcp(*argv[1:2]), ensure_ascii=False, indent=2))
        return 0
    print("用法: python -m app.collectors.xhs_sample probe [keyword] | probe-mcp [keyword]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
