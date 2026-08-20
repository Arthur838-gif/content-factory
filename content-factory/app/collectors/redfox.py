"""RedFoxHub（红狐数据 redfox.hk）小红书只读数据源（P-1b 补位）。

背景：xiaohongshu-mcp 的 search_feeds 不含作者 fans，user_profile 又会
60s panic 触发风控；RedFox 爆款洞察接口的搜索结果自带 authorFans 与
四项互动计数，一次调用即可进入低粉爆款判定。本模块只做只读查询。

接口（见仓库外 redfox-api文档/，2026-08-18 拉取）：
- 爆款笔记洞察 POST /story/api/xhs/search/search —— 主采样；
- 作品内容详情 POST /story/api/xhsUser/queryWorkDetail —— 候选深挖
  （全文 workDesc + 阅读量 workReadedCount），暂供 CLI 手动使用；
- 违禁词检测 POST /story/api/cozeSkill/sensitiveWordSearch —— 文章发布前
  手动体检（按调用计费），契约取自 redfox-skills/xiaohongshu-prohibited-word
  的实测脚本（X-API-KEY 头 + content/platform/source 载荷）。
- 公众号优质库（P9，文档 2026-08-20 拉取）：
  POST /story/api/gzhData/searchArticle 关键词搜文章（腰部以上近 30 天，
  列表自带正文与全量互动计数，无粉丝字段）；
  POST /story/api/gzhData/queryArticleDetail 按文章地址取详情（人工喂样本）。

鉴权：请求头 REDFOX_API_KEY（redfox.hk 密钥管理页创建），按调用计费；
违禁词接口按 skill 实测另带 X-API-KEY 头（cozeSkill 系与洞察系鉴权头并存）。
响应包装不统一：详情接口带 {code:2000,msg,data}，洞察接口示例顶层裸给
（articles 直接在顶层），_unwrap 两种都兼容，首次联调需实测确认。
"""
import json
import logging
import re
from datetime import date, timedelta

import httpx

from .. import config
from ..schemas import HotItem

logger = logging.getLogger(__name__)

INSIGHT_PATH = "/story/api/xhs/search/search"
WORK_DETAIL_PATH = "/story/api/xhsUser/queryWorkDetail"
SEVEN_PATH = "/story/api/cozeSkill/getXhsCozeSkillDataSeven"
SEARCH_USER_PATH = "/story/api/xhsUser/searchUser"
SENSITIVE_PATH = "/story/api/cozeSkill/sensitiveWordSearch"
GZH_SEARCH_ARTICLE_PATH = "/story/api/gzhData/searchArticle"
GZH_ARTICLE_DETAIL_PATH = "/story/api/gzhData/queryArticleDetail"

# 七日爆款接口的官方类目枚举（文档 2026-08-19 拉取；建栏目的领域下拉与
# 关键词推荐来源）。「综合全部」是查询用通配值，不作为内容领域，排除。
XHS_CATEGORIES = [
    "学习教育", "职业发展", "星座情感", "数码科技", "化妆美容", "时尚穿搭",
    "旅行度假", "亲子育儿", "美味佳肴", "宠物天地", "居家装修", "医疗保健",
    "个人护理", "体育锻炼", "影视娱乐", "休闲爱好", "拍摄记录", "婚庆婚礼",
    "新闻资讯", "科学探索", "潮流鞋包", "出行代步", "日常生活", "综合杂项",
]


class RedFoxError(RuntimeError):
    """RedFox 请求失败（网络 / 鉴权 / 业务码非 2000 / 响应形状异常）。"""


def _to_int(value) -> int:
    """互动数字段容错解析：int / "1234" / "1,200" / "1.2万" / "5w+" / None → int。

    七日爆款榜的 "5w+" 之类模糊量级按下界取值（50000），只用于量级参考。
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"([\d.]+)\s*[万wW]", text)
    if match:
        return int(float(match.group(1)) * 10000)
    match = re.search(r"\d+(\.\d+)?", text)
    return int(float(match.group())) if match else 0


def enabled() -> bool:
    """配置了 API Key 即启用 RedFox 优先采样（xhs_sample 双源调度用）。"""
    return bool(config.REDFOX_API_KEY)


def _unwrap(resp, what: str):
    """剥 {code,msg,data} 包装；顶层裸响应原样返回。code 非 2000 抛 RedFoxError。"""
    if not isinstance(resp, dict):
        raise RedFoxError(f"{what} 响应非对象：{type(resp).__name__}")
    if "code" in resp:
        if resp.get("code") != 2000:
            raise RedFoxError(
                f"{what} 业务失败：code={resp.get('code')} "
                f"msg={resp.get('msg') or resp.get('message')}"
            )
        return resp.get("data")
    return resp


def _post(path: str, payload: dict, extra_headers: dict | None = None):
    headers = {
        "REDFOX_API_KEY": config.REDFOX_API_KEY,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    # 连接建立快败（网络不通没必要等满超时），慢响应等满 REDFOX_TIMEOUT_SECONDS
    timeout = httpx.Timeout(config.REDFOX_TIMEOUT_SECONDS, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{config.REDFOX_BASE_URL}{path}", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise RedFoxError(f"{path} 网络错误：{exc}") from exc
    if resp.status_code in (401, 403):
        raise RedFoxError(f"{path} 鉴权失败（HTTP {resp.status_code}）：检查 REDFOX_API_KEY 有效性与余额")
    if resp.status_code >= 400:
        raise RedFoxError(f"{path} HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RedFoxError(f"{path} 响应非 JSON：{resp.text[:200]}") from exc


def _get(path: str, params: dict):
    """GET 版请求（七日爆款等查询接口），鉴权/错误处理与 _post 一致。"""
    headers = {"REDFOX_API_KEY": config.REDFOX_API_KEY}
    timeout = httpx.Timeout(config.REDFOX_TIMEOUT_SECONDS, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{config.REDFOX_BASE_URL}{path}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise RedFoxError(f"{path} 网络错误：{exc}") from exc
    if resp.status_code in (401, 403):
        raise RedFoxError(f"{path} 鉴权失败（HTTP {resp.status_code}）：检查 REDFOX_API_KEY 有效性与余额")
    if resp.status_code >= 400:
        raise RedFoxError(f"{path} HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RedFoxError(f"{path} 响应非 JSON：{resp.text[:200]}") from exc


def search_articles(keyword: str, days: int | None = None) -> list[dict]:
    """爆款洞察：关键词搜索，返回 articles 原始列表。

    只取 articles；latestHotArticles 是搜索结果不足时的全站推荐兜底，
    与检索词无关，采了会污染领域过滤，一律丢弃。时间窗默认近 7 天
    （低粉爆款只追近期数据，也压低按调用计费的单轮成本）。
    """
    days = config.REDFOX_WINDOW_DAYS if days is None else days
    end = date.today()
    payload = {
        "keyword": keyword,
        "pageSize": 50,
        "startDate": (end - timedelta(days=days)).isoformat(),
        "endDate": end.isoformat(),
    }
    data = _unwrap(_post(INSIGHT_PATH, payload), "爆款洞察")
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        raise RedFoxError(f"爆款洞察响应缺少 articles 列表：{str(data)[:200]}")
    return [a for a in articles if isinstance(a, dict)]


def parse_articles(articles: list[dict]) -> list[HotItem]:
    """洞察 articles → HotItem 列表（无标题条目跳过，URL 缺省按笔记 ID 构造）。"""
    items: list[HotItem] = []
    for article in articles:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        note_id = str(article.get("id") or "")
        url = str(article.get("shareInfoLink") or "") or (
            f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
        )
        items.append(
            HotItem(
                source="xhs",
                title=title,
                url=url,
                author=str(article.get("authorNickname") or "") or None,
                fans=_to_int(article.get("authorFans")),
                likes=_to_int(article.get("likedCount")),
                collects=_to_int(article.get("collectedCount")),
                comments=_to_int(article.get("commentsCount")),
                raw={"article": article},
            )
        )
    return items


def search_hot_items(keyword: str) -> list[HotItem]:
    """主入口：洞察搜索 + 归一化，供 xhs_sample 优先调用。"""
    return parse_articles(search_articles(keyword))


def seven_day_hot(category: str = "综合全部", rank_date: str | None = None) -> list[dict]:
    """七日爆款笔记：按官方类目拉最近 7 天的爆款榜（每天 19:00 更新昨日榜）。

    供建栏目的关键词推荐（标题/正文 hashtag 词频）与对标参考，不直接入库。
    实测（2026-08-19）：19 点前昨日榜尚未生成（返回空列表），自动回退取
    前天；显式指定 rank_date 时不回退。
    """
    def fetch_once(day: str | None) -> list[dict]:
        params = {"category": category}
        if day:
            params["rankDate"] = day
        data = _unwrap(_get(SEVEN_PATH, params), "七日爆款")
        if not isinstance(data, list):
            raise RedFoxError(f"七日爆款响应缺少 data 列表：{str(data)[:200]}")
        return [note for note in data if isinstance(note, dict)]

    if rank_date:
        return fetch_once(rank_date)
    for days_ago in (1, 2):
        notes = fetch_once((date.today() - timedelta(days=days_ago)).isoformat())
        if notes:
            return notes
    return []


def search_accounts(keyword: str, offset: int = 0, sort_type: str = "_4") -> list[dict]:
    """按关键词搜小红书账号（优质库）：建栏目的对标账号参考。

    sortType：_0 相关性 / _2 最新 / _4 最热（红狐指数），默认最热。
    """
    payload = {"keyword": keyword, "offset": offset, "sortType": sort_type}
    data = _unwrap(_post(SEARCH_USER_PATH, payload), "账号搜索")
    rows = data.get("list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RedFoxError(f"账号搜索响应缺少 list 列表：{str(data)[:200]}")
    return [account for account in rows if isinstance(account, dict)]


def work_detail(work_id: str = "", work_link: str = "") -> dict:
    """作品详情（优质库）：全文、精确互动计数与阅读量。work_id / work_link 二选一。"""
    payload = {k: v for k, v in {"workId": work_id, "workLink": work_link}.items() if v}
    if not payload:
        raise RedFoxError("work_detail 需要 workId 或 workLink 至少一项")
    data = _unwrap(_post(WORK_DETAIL_PATH, payload), "作品详情")
    if not isinstance(data, dict):
        raise RedFoxError("作品详情响应缺少 data 对象")
    return data


# ---- 公众号优质库（P9；只读查询，按调用计费）----
def gzh_search_articles(keyword: str, offset: int = 0, sort_type: str = "_4") -> list[dict]:
    """公众号文章搜索（优质库，腰部以上公众号近 30 天，列表自带正文）。

    sortType：_0 相关性 / _2 最新 / _4 最热（按阅读数倒序）——默认最热，
    与爆款采样「找互动异常高的内容」的目标一致。每页 20 条 1 次计费；
    翻页用 offset（每页 +20），深采留给后续需要。
    """
    payload = {"keyword": keyword, "offset": offset, "sortType": sort_type}
    data = _unwrap(_post(GZH_SEARCH_ARTICLE_PATH, payload), "公众号搜索")
    rows = data.get("list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RedFoxError(f"公众号搜索响应缺少 list 列表：{str(data)[:200]}")
    return [article for article in rows if isinstance(article, dict)]


def parse_gzh_articles(articles: list[dict]) -> list[HotItem]:
    """公众号 searchArticle 条目 → HotItem。

    fans 恒 0：公众号接口无粉丝字段，账号规模维度推迟（docs 记录）；
    阅读数/在看/分享等完整指标留在 raw.article，由 gzh 判定管线读取。
    无标题或无 workUrl 的条目跳过（URL 是去重键，缺了无法去重）。
    """
    items: list[HotItem] = []
    for article in articles:
        title = str(article.get("title") or "").strip()
        url = str(article.get("workUrl") or "").strip()
        if not title or not url:
            continue
        items.append(
            HotItem(
                source="gzh",
                title=title,
                url=url,
                author=str(article.get("author") or "") or None,
                fans=0,
                likes=_to_int(article.get("likeCount")),
                collects=_to_int(article.get("collectCount")),
                comments=_to_int(article.get("commentCount")),
                raw={"article": article},
            )
        )
    return items


def search_gzh_items(keyword: str, offset: int = 0, sort_type: str = "_4") -> list[HotItem]:
    """主入口：公众号搜索 + 归一化，供 gzh_sample 优先调用。"""
    return parse_gzh_articles(gzh_search_articles(keyword, offset=offset, sort_type=sort_type))


def gzh_article_detail(url: str) -> dict:
    """公众号作品详情（优质库）：按文章地址取全量指标与正文。

    人工喂样本用（1 次计费，页面 confirm 后才触发）；返回原始 data 对象
    （与列表条目同形，含 content 全文），字段解析由调用方做。
    """
    url = (url or "").strip()
    if not url:
        raise RedFoxError("gzh_article_detail 需要文章地址")
    data = _unwrap(_post(GZH_ARTICLE_DETAIL_PATH, {"url": url}), "公众号作品详情")
    if not isinstance(data, dict):
        raise RedFoxError("公众号作品详情响应缺少 data 对象")
    return data


# 违禁词在标注 HTML 里的三种风险级 span 类名（skill 实测脚本同款正则）
_SENSITIVE_SPAN = re.compile(
    r'<span class="(?:banned-word|sensitive-word|industry-banned-word)">(.*?)</span>'
)
SENSITIVE_MAX_CHARS = 3000


def sensitive_word_search(content: str, platform: str = "小红书") -> dict:
    """违禁词检测（按调用计费，只读；发布前手动体检用）。

    platform 实测过「小红书」；「微信公众号」为 P9 待验证值（真实验收
    确认，若上游不支持则公众号体检回滚为不可用）。载荷/解析契约取自
    redfox-skills/xiaohongshu-prohibited-word 的实测脚本：
    返回 data.content 为 HTML 标注原文（命中词包在风险级 span 里），
    prohibitedWordsType 为风险分类列表。返回 {words, categories}；
    words 保序去重。调用方负责确认计费（页面 confirm 后才触发）。
    """
    if not enabled():
        raise RedFoxError("未配置 REDFOX_API_KEY（.env 的 CF_REDFOX_API_KEY），无法体检")
    content = (content or "").strip()
    if not content:
        raise RedFoxError("待检测内容为空")
    if len(content) > SENSITIVE_MAX_CHARS:
        raise RedFoxError(
            f"内容 {len(content)} 字超检测上限 {SENSITIVE_MAX_CHARS} 字，请缩减后重试"
        )
    resp = _unwrap(
        _post(
            SENSITIVE_PATH,
            # source 沿用 skill 实测原值构造（小红书时与实测脚本逐字一致），
            # 不改未知契约
            {"content": content, "platform": platform, "source": f"{platform}违禁词查询-GitHub"},
            extra_headers={"X-API-KEY": config.REDFOX_API_KEY},
        ),
        "违禁词检测",
    )
    data = resp if isinstance(resp, dict) else {}
    words = list(dict.fromkeys(_SENSITIVE_SPAN.findall(str(data.get("content") or ""))))
    # 英文误匹配过滤（skill 实测脚本同款）：纯英文命中词若是内容里某英文单词
    # 的子串（如 "av" ⊂ "Gravitas"）即为误报，剔除——否则用户把它回填进本地
    # 词表后，所有含该子串的文章都会被本地方向误杀
    original = str(data.get("originalContent") or content)
    english_words = [w.lower() for w in re.findall(r"[A-Za-z]+", original)]
    false_positives = {
        w for w in words
        if w.isascii() and w.isalpha()
        and any(w.lower() in ew and w.lower() != ew for ew in english_words)
    }
    if false_positives:
        words = [w for w in words if w not in false_positives]
        logger.info("违禁词检测剔除英文子串误报：%s", "、".join(sorted(false_positives)))
    categories = [
        str(c) for c in (data.get("prohibitedWordsType") or []) if str(c).strip()
    ]
    logger.info("违禁词检测完成：%d 个命中词 / %d 个风险分类", len(words), len(categories))
    return {"words": words, "categories": categories}


def probe(keyword: str = "AI工具") -> dict:
    """RedFox 探针：验证 Key 可用 + 搜索结果是否真的带 fans 字段。

    用法：python -m app.collectors.redfox probe [keyword]
    """
    items = search_hot_items(keyword)
    fans_values = [item.fans for item in items]
    return {
        "source": "redfox",
        "keyword": keyword,
        "note_count": len(items),
        "fans_available": any(fans > 0 for fans in fans_values),
        "fans_found": sum(1 for fans in fans_values if fans > 0),
        "conclusion": (
            "RedFox 搜索结果含 authorFans：低粉爆款判定可直接跑通"
            if any(fans > 0 for fans in fans_values)
            else "RedFox 搜索可用但未见 fans：响应字段与文档不符，需人工核对原始报文"
        ),
    }


def main(argv: list[str]) -> int:
    if argv and argv[0] == "probe":
        if not enabled():
            print("未配置 REDFOX_API_KEY（.env 加 CF_REDFOX_API_KEY 或环境变量 REDFOX_API_KEY）")
            return 2
        print(json.dumps(probe(*argv[1:2]), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "detail" and len(argv) > 1:
        target = argv[1]
        detail = work_detail(
            work_id="" if target.startswith("http") else target,
            work_link=target if target.startswith("http") else "",
        )
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0
    print("用法: python -m app.collectors.redfox probe [keyword] | detail <笔记ID或链接>")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
