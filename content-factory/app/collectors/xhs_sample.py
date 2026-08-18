"""M2 小红书采样器（P-1b）：经 xiaohongshu-mcp 只读采样。

纪律（任务契约）：
- 只调用 search_notes 等只读工具；禁止任何写接口、互动接口与账号行为
  （关注、点赞、评论、私信、回关、发布一律不碰）。
- 数据小号凭据只进本地 mcp 服务，本仓库不存 Cookie / 二维码 / 账号信息。
- 搜索结果若无作者 fans 字段（见 docs/p-1b-fans-probe.md 探针结论），
  条目仍落 hot_items 笔记级数据，但低粉爆款判定跳过（fans 未知不伪造），
  由人工喂样本（POST /api/viral-samples/manual）补齐 fans 后进入同一管线。
"""
import json
import logging
import re

import httpx

from .. import config
from ..schemas import HotItem
from .base import BaseCollector, register_collector

logger = logging.getLogger(__name__)

# 搜索结果里可能承载粉丝数的键（探针确认实际字段后收敛）
_FANS_KEYS = ("fans", "fans_count", "fan_count", "follower_count", "followers")


class McpHttpError(RuntimeError):
    """xiaohongshu-mcp 请求失败（网络 / JSON-RPC 错误 / 工具报错）。"""


def _to_int(value) -> int:
    """互动数字段容错解析：int / "1234" / "1.2万" / None → int。"""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"([\d.]+)\s*万", text)
    if match:
        return int(float(match.group(1)) * 10000)
    match = re.search(r"\d+(\.\d+)?", text)
    return int(float(match.group())) if match else 0


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


def parse_search_notes(notes: list) -> list[HotItem]:
    """mcp search_notes 结果 → HotItem 列表（无标题的条目跳过）。"""
    items: list[HotItem] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
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

    用法：python -m app.collectors.xhs_sample probe [keyword]
    """
    notes = call_mcp_tool("search_notes", {"keyword": keyword})
    parsed = parse_search_notes(notes)
    fans_values = [item.fans for item in parsed]
    return {
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
    """M2 小红书采样器：逐关键词调只读搜索，聚合为 HotItem 列表。

    URL 去重与领域过滤在 base.persist_hot_items 统一做；本类只负责拉取。
    """

    name = "xhs_sample"

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        for keyword in self._queries():
            notes = self._search(keyword)
            parsed = parse_search_notes(notes)
            logger.info("xhs 采样 %s：%s 条笔记", keyword, len(parsed))
            items.extend(parsed)
        return items

    def _search(self, keyword: str) -> list[dict]:
        # 只读搜索工具；写/互动类工具一律不调用（任务契约 1）
        return call_mcp_tool("search_notes", {"keyword": keyword})

    def _queries(self) -> list[str]:
        if config.XHS_SAMPLE_KEYWORDS:
            return list(config.XHS_SAMPLE_KEYWORDS)
        keywords: list[str] = []
        for domain_keywords in _domain_keywords().values():
            keywords.extend(domain_keywords)
        return keywords[: config.XHS_SAMPLE_MAX_QUERIES]


def _domain_keywords() -> dict[str, list[str]]:
    # 延迟导入避免 collectors.base ← radar 的导入环
    from ..services import radar

    return radar.load_domains()


def main(argv: list[str]) -> int:
    if argv and argv[0] == "probe":
        print(json.dumps(probe_fans(*argv[1:2]), ensure_ascii=False, indent=2))
        return 0
    print("用法: python -m app.collectors.xhs_sample probe [keyword]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
