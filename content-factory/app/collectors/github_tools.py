"""GitHub 开源项目采集器（P7）：给合集栏目提供真实可核验的工具素材。

动机：合集类栏目（"本周5个值得装的AI工具"）此前只能引用小红书笔记标题，
模型只能靠编"工具名"凑数。GitHub search API 返回的仓库名/链接/star 数
全是真实数据——推荐真实存在的项目，读者搜得到、留得住。

数据与纪律：
- 只读公开 search API（api.github.com/search/repositories），无鉴权
  （限额 10 次/分钟，按栏目关键词量级足够；不调任何写接口）；
- 时效优先：限定"最近 GITHUB_DAYS_CREATED 天内创建"再按 star 倒序——
  新建却能快速攒星的项目才是真新锐；不按全历史 star 排（老牌项目霸榜）；
- 条目映射：likes=star 数、collects=fork 数；raw.keyword 记栏目中文关键词
  （与 xhs_sample 同约定，pillar 排期靠它命中），raw.created_at/pushed_at
  供生成时写出"上周刚开源"这类时效语境；
- 标题带上中文关键词（"{关键词}开源项目｜owner/repo：简介"），
  一是为了领域词表能命中入库，二是给主题规划/生成提供可读上下文。
"""
import logging
from datetime import date, timedelta

import httpx

from .. import config
from ..schemas import HotItem
from .base import BaseCollector, register_collector

logger = logging.getLogger(__name__)

# 栏目中文关键词 → GitHub 查询词（栏目词原样进 raw.keyword 供排期命中）
_ZH_EN = {
    "ai工具": "AI tools",
    "ai": "AI",
    "人工智能": "artificial intelligence",
    "大模型": "LLM",
    "智能体": "AI agent",
    "ai绘画": "AI image generation",
    "ai视频": "AI video generation",
}

_API = "https://api.github.com/search/repositories"


def to_hot_items(repos: list, zh_kw: str) -> list[HotItem]:
    """search API 条目 → HotItem（star 数不达门槛 / 无名的跳过）。"""
    items: list[HotItem] = []
    for r in repos:
        if not isinstance(r, dict):
            continue
        full = str(r.get("full_name") or "").strip()
        stars = int(r.get("stargazers_count") or 0)
        if not full or stars < config.GITHUB_MIN_STARS:
            continue
        desc = str(r.get("description") or "").strip()
        items.append(
            HotItem(
                source="github",
                title=f"{zh_kw}开源项目｜{full}：{desc}"[:512],
                url=str(r.get("html_url") or f"https://github.com/{full}"),
                author=(r.get("owner") or {}).get("login"),
                likes=stars,
                collects=int(r.get("forks_count") or 0),
                comments=int(r.get("open_issues_count") or 0),
                raw={
                    "keyword": zh_kw,
                    "full_name": full,
                    "stars": stars,
                    "description": desc,
                    "topics": r.get("topics") or [],
                    "language": r.get("language"),
                    "created_at": r.get("created_at"),
                    "pushed_at": r.get("pushed_at"),
                },
            )
        )
    return items


@register_collector
class GithubToolsCollector(BaseCollector):
    name = "github_tools"

    def fetch(self) -> list[HotItem]:
        items: list[HotItem] = []
        for zh_kw, query in self._queries():
            repos = self._search(query)
            parsed = to_hot_items(repos, zh_kw)
            logger.info("github 采样 %s（%s）：%s 个项目", zh_kw, query, len(parsed))
            items.extend(parsed)
        return items

    def _search(self, query: str) -> list:
        created_since = (date.today() - timedelta(days=config.GITHUB_DAYS_CREATED)).isoformat()
        pushed_since = (date.today() - timedelta(days=config.GITHUB_DAYS_PUSHED)).isoformat()
        with httpx.Client(timeout=config.GITHUB_TIMEOUT_SECONDS) as client:
            resp = client.get(
                _API,
                params={
                    "q": (f"{query} stars:>{config.GITHUB_MIN_STARS}"
                          f" created:>{created_since} pushed:>{pushed_since}"),
                    "sort": "stars",
                    "order": "desc",
                    "per_page": config.GITHUB_PER_QUERY,
                },
                headers={"Accept": "application/vnd.github+json"},
            )
        if resp.status_code == 403:
            raise RuntimeError("GitHub API 限流（未鉴权 10 次/分钟），稍后再试")
        resp.raise_for_status()
        return resp.json().get("items") or []

    def _queries(self) -> list[tuple[str, str]]:
        """(栏目中文关键词, GitHub 查询) 对；显式配置 > 栏目关键词池 > 默认。"""
        if config.GITHUB_QUERIES:
            out = []
            for part in config.GITHUB_QUERIES:
                if "=" in part:
                    zh, q = part.split("=", 1)
                    out.append((zh.strip(), q.strip()))
                else:
                    out.append((part.strip(), part.strip()))
            return out
        from ..db import session_scope
        from ..services import pillar as pillar_service

        with session_scope() as session:
            keywords = pillar_service.pillar_keywords(session)
        out = []
        for kw in keywords:
            if kw.isascii():
                out.append((kw, kw))
            elif mapped := _ZH_EN.get(kw.lower()):
                out.append((kw, mapped))
        return (out or [("AI工具", "AI tools")])[: config.GITHUB_MAX_QUERIES]
