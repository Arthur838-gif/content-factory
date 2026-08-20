"""M2 小红书采样器（P-1b）：RedFox 爆款洞察单源只读采样。

数据源：RedFox（app.collectors.redfox，需 REDFOX_API_KEY，按调用计费），
搜索结果自带 authorFans，低粉爆款判定直接跑通。曾并存的
xiaohongshu-mcp 本地降级源已于 2026-08-20 废弃：本地服务常年未部署，
降级只会把 RedFox 的真实故障（超时/504）盖成「连接被拒绝」，且其
搜索结果不含 fans（docs/p-1b-fans-probe.md）。

纪律（任务契约）：
- 只调用只读查询；禁止任何写接口、互动接口与账号行为
  （关注、点赞、评论、私信、回关、发布一律不碰）。
"""
import json
import logging

from .. import config
from ..schemas import HotItem
from ..services import domain_service
from .base import BaseCollector, register_collector
from .redfox import RedFoxError, enabled as redfox_enabled, probe as redfox_probe, search_hot_items

logger = logging.getLogger(__name__)


def probe_fans(keyword: str = "AI工具") -> dict:
    """第 0 步 fans 字段探针（只读）。结论记录进 docs/p-1b-fans-probe.md。

    用法：python -m app.collectors.xhs_sample probe [keyword]
    """
    if redfox_enabled():
        try:
            return redfox_probe(keyword)
        except RedFoxError as exc:
            return {
                "source": "redfox",
                "keyword": keyword,
                "error": str(exc),
                "conclusion": "RedFox 调用失败：检查 Key 有效性 / 余额 / 网络后重试",
            }
    return {
        "source": "redfox",
        "keyword": keyword,
        "error": "未配置 REDFOX_API_KEY",
        "conclusion": "RedFox 是唯一采样源：在 .env 配置 REDFOX_API_KEY 后重跑探针",
    }


@register_collector
class XhsSampleCollector(BaseCollector):
    """M2 小红书采样器：逐关键词采样，聚合为 HotItem 列表。

    RedFox 失败即该关键词失败（无降级源），由调用方计入熔断。
    URL 去重与领域过滤在 base.persist_hot_items 统一做；本类只负责拉取。
    """

    name = "xhs_sample"

    def __init__(self, keywords: list[str] | None = None):
        # 定向采样（新建栏目后自动补素材）时显式传关键词，绕过 _queries
        # 的"栏目关键词池 / 领域词表"推导
        self._keywords_override = [k for k in (keywords or []) if k][: config.XHS_SAMPLE_MAX_QUERIES]

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        for keyword in self._queries():
            parsed, source = self.fetch_keyword(keyword)
            # 记录命中该条的检索词：pillar 排期按标题或采样词匹配（标题未必含关键词）
            for item in parsed:
                raw = dict(item.raw or {})
                raw.setdefault("keyword", keyword)
                item.raw = raw
            logger.info("xhs 采样 %s（%s）：%s 条笔记", keyword, source, len(parsed))
            items.extend(parsed)
        return items

    def fetch_keyword(self, keyword: str) -> tuple[list[HotItem], str]:
        """单关键词采样（worker 逐词调用，进度可逐词落库）：
        RedFox 单源（搜索结果自带 fans）。未配 Key 或调用失败直接抛
        RedFoxError——无降级源，静默跳过会掩盖配置缺失与真实故障。
        返回 (条目列表, 数据源 redfox)。
        """
        if not redfox_enabled():
            raise RedFoxError("RedFox 未配置 API Key（.env 的 REDFOX_API_KEY），无法采样")
        return search_hot_items(keyword), "redfox"

    def _queries(self) -> list[str]:
        # 优先级：显式传入（定向采样）> 环境变量 > 启用栏目的关键词池（P5，栏目驱动采样）> 领域词表
        if self._keywords_override:
            return list(self._keywords_override)
        if config.XHS_SAMPLE_KEYWORDS:
            return list(config.XHS_SAMPLE_KEYWORDS)
        from ..db import session_scope
        from ..services import pillar as pillar_service

        with session_scope() as session:
            keywords = pillar_service.pillar_keywords(session)
        if keywords:
            return keywords[: config.XHS_SAMPLE_MAX_QUERIES]
        keywords = []
        for domain_keywords in domain_service.load_domains().values():
            keywords.extend(domain_keywords)
        return keywords[: config.XHS_SAMPLE_MAX_QUERIES]


def main(argv: list[str]) -> int:
    if argv and argv[0] == "probe":
        print(json.dumps(probe_fans(*argv[1:2]), ensure_ascii=False, indent=2))
        return 0
    print("用法: python -m app.collectors.xhs_sample probe [keyword]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
