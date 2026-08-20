"""P9 公众号采样器：RedFox 优质库 searchArticle 单源只读采样。

数据源：RedFox 公众号优质库（app.collectors.redfox，按调用计费），
覆盖腰部以上公众号近 30 天，列表自带正文与全量互动计数；无粉丝字段，
爆款判定用阅读量下限 + 互动密度（radar.process_gzh_item），不复制
小红书的「低粉」口径。

纪律（与 xhs_sample 同契约）：
- 只调用只读查询；禁止任何写接口、互动接口与账号行为。
- 每关键词 1 页 20 条 = 1 次计费；无 Key 或调用失败直接抛 RedFoxError，
  静默跳过会掩盖配置缺失与真实故障。
"""
import logging

from .. import config
from ..schemas import HotItem
from ..services import domain_service
from .base import BaseCollector, register_collector
from .redfox import RedFoxError, enabled as redfox_enabled, search_gzh_items

logger = logging.getLogger(__name__)


@register_collector
class GzhSampleCollector(BaseCollector):
    """公众号采样器：逐关键词采样，聚合为 HotItem 列表。

    RedFox 失败即该关键词失败（无降级源），由调用方计入熔断。
    URL 去重与领域过滤在 base.persist_hot_items 统一做；本类只负责拉取。
    """

    name = "gzh_sample"

    def __init__(self, keywords: list[str] | None = None):
        # 定向采样时显式传关键词，绕过 _queries 的"栏目词池 / 领域词表"推导
        self._keywords_override = [k for k in (keywords or []) if k][: config.GZH_SAMPLE_MAX_QUERIES]

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        for keyword in self._queries():
            parsed, source = self.fetch_keyword(keyword)
            # 记录命中该条的检索词：标题未命中词表时按采样词反查领域放行
            for item in parsed:
                raw = dict(item.raw or {})
                raw.setdefault("keyword", keyword)
                item.raw = raw
            logger.info("gzh 采样 %s（%s）：%s 篇文章", keyword, source, len(parsed))
            items.extend(parsed)
        return items

    def fetch_keyword(self, keyword: str) -> tuple[list[HotItem], str]:
        """单关键词采样（worker 逐词调用，进度可逐词落库）。

        sortType=_4 最热（按阅读数倒序），与爆款采样的目标一致。
        返回 (条目列表, 数据源 redfox)。
        """
        if not redfox_enabled():
            raise RedFoxError("RedFox 未配置 API Key（.env 的 REDFOX_API_KEY），无法采样")
        return search_gzh_items(keyword), "redfox"

    def _queries(self) -> list[str]:
        # 优先级与 xhs_sample 同口径：显式传入 > 环境变量 > 启用栏目的关键词池 > 领域词表
        if self._keywords_override:
            return list(self._keywords_override)
        if config.GZH_SAMPLE_KEYWORDS:
            return list(config.GZH_SAMPLE_KEYWORDS)
        from ..db import session_scope
        from ..services import pillar as pillar_service

        with session_scope() as session:
            keywords = pillar_service.pillar_keywords(session)
        if keywords:
            return keywords[: config.GZH_SAMPLE_MAX_QUERIES]
        keywords = []
        for domain_keywords in domain_service.load_domains().values():
            keywords.extend(domain_keywords)
        return keywords[: config.GZH_SAMPLE_MAX_QUERIES]
