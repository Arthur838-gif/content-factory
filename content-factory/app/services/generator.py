"""M5 内容生成服务骨架（计划书第 7 章 M5 / SDD 3.2 M5）。

职责：调 LLM 产出结构化 JSON，Pydantic 校验、失败重试（封顶 2 次）、敏感词过滤，
最终产出 articles 行所需的 (内容对象, meta.usage, error)。

设计要点：
- LLM 客户端只依赖 OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME 三个环境变量，
  httpx 直连 OpenAI 兼容协议 /chat/completions + response_format=json_object，免引 SDK。
- 无 Key 降级（config.LLM_MOCK）：返回符合 Schema 的固定 JSON，usage 写占位值（model="mock"）。
  mock 与真实路径并列、由开关分流；mock 是脚手架，不让真实路径走样。
- 重试：最多 2 次重试（共 3 轮），重试时把上一轮错误信息追加进 user 消息。
- 成本记账：meta.usage = {prompt_tokens, completion_tokens, model, cost_est}，每次生成必写。
- 超时与 max_tokens 上限集中在 config.py（第 8.3 节）。
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .. import config
from . import sensitive

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class GenerationResult:
    """一次生成的产物与成本。article 为 None 即生成失败（error 必填）。"""

    article: object | None
    usage: dict
    error: str | None = None
    sensitive_hits: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.article is not None and not self.sensitive_hits


# ---- mock 降级（脚手架，非产品行为）----
_MOCK_WECHAT = {
    "title": "深度解读：信息密度高，一文看懂核心要点",
    "digest": "摘要：从背景到结论，快速抓住这件事的关键脉络与可执行建议。",
    "content_md": (
        "# 深度解读\n\n"
        "## 背景\n\n这件事为什么值得关注。信息密度高，直击要害。\n\n"
        "## 核心论点\n\n"
        "1. 第一个论点：有数据、有案例支撑。\n"
        "2. 第二个论点：逻辑递进，观点有证据。\n"
        "3. 第三个论点：拒绝标题党和空洞煽情。\n\n"
        "## 案例\n\n一处具体案例：用真实场景说明论点成立。\n\n"
        "## 可执行建议\n\n结尾给出读者能直接上手的动作清单。"
    ),
}

# 刻意满足 P1 量规：标题 ≤20 字含 emoji、正文口语化每段 ≤3 行（300-800 字）、
# 标签 3-5 个不带 #、金句 2-4 句每句 ≤20 字——mock 模式下结构验收与 M7 拼标签都有据可验。
_MOCK_XHS = {
    "title": "试了一圈AI工具，真的回不去了🥲",
    "content": (
        "最近试了一圈AI工具，真的回不去了🥲\n\n"
        "以前写方案要憋一整天，现在先把思路丢给AI，10分钟出初稿。"
        "我再补点细节、改改语气，半小时收工。\n\n"
        "分享3个我天天在用的👇\n\n"
        "1️⃣ 写作助手：大纲、初稿、改语气，一句话的事。"
        "卡壳时让它给3个角度，总有一个能抄作业\n\n"
        "2️⃣ 会议纪要：录音转文字自动总结。"
        "再也不用边开会边狂记，会后直接拿结论去推进\n\n"
        "3️⃣ 数据整理：表格丢进去，让它找规律出图表。"
        "以前手动核对两小时，现在泡杯咖啡的功夫\n\n"
        "重点来了：工具只是放大器💡。思路清晰的人用它起飞，没思路的人只会复制粘贴\n\n"
        "我的习惯是先写清楚\"我要什么结果\"，再让AI干活。prompt越具体，产出越能打\n\n"
        "别再闷头加班了，先把工具用起来。省下来的时间摸鱼、学习、搞副业，不香吗😌\n\n"
        "你们还有什么私藏工具？评论区交换一下情报👇"
    ),
    "tags": ["AI工具", "效率提升", "打工人", "摸鱼"],
    "cover_text": "AI工具实测🔥",
    "image_quotes": ["工具用得好，下班走得早", "别让重复劳动吃掉人生", "先理清思路，再让AI干活"],
}

# P-1b 周度拆解 mock：只验证管线结构（reason 回写 + 标签热度累计），非真实质量依据
_MOCK_TEARDOWN = {
    "title_patterns": ["数字清单型：N个方法搞定一个具体问题", "反差型：低粉小号也能爆的垂直切口"],
    "emotion_words": ["真的回不去了", "亲测有效", "直接抄作业", "别再闷头", "省下的时间"],
    "structures": ["痛点开头 → 数字清单主体 → 互动提问结尾", "个人经历开头 → 方法拆解 → 结果展示"],
    "tags": [{"domain": "AI与编程", "tag": "AI工具实测"}],
    "samples": [],
}


# P5b 周主题规划 mock：只验证管线结构（WeekTheme 落库 + 分期建题），非真实质量依据
_MOCK_THEME_PLAN = {
    "theme": "AI 效率工具实战周",
    "subtopics": [
        {"title": "AI 写作提效的真实边界", "hot_item_ids": []},
        {"title": "会议纪要自动化小组合", "hot_item_ids": []},
    ],
}


def _mock_article(schema_cls: type[T]) -> T:
    """返回一份符合指定 Schema 的固定 JSON（P0 WechatArticle，P1 XhsNote，P-1b ViralTeardown，P5b WeekThemePlan）。"""
    name = schema_cls.__name__
    data = (
        _MOCK_WECHAT
        if name == "WechatArticle"
        else _MOCK_XHS
        if name == "XhsNote"
        else _MOCK_TEARDOWN
        if name == "ViralTeardown"
        else _MOCK_THEME_PLAN
        if name == "WeekThemePlan"
        else None
    )
    if data is None:
        raise ValueError(f"mock 降级未覆盖 Schema：{name}")
    return schema_cls.model_validate(data)


def _mock_usage() -> dict:
    return {
        "model": "mock",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_est": 0.0,
    }


# ---- 成本估算 ----
def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        prompt_tokens / 1_000_000 * config.LLM_PRICE_INPUT_PER_M
        + completion_tokens / 1_000_000 * config.LLM_PRICE_OUTPUT_PER_M,
        6,
    )


def _usage_from_response(resp_usage: dict) -> dict:
    pt = int(resp_usage.get("prompt_tokens", 0) or 0)
    ct = int(resp_usage.get("completion_tokens", 0) or 0)
    return {
        "model": config.MODEL_NAME,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cost_est": _estimate_cost(pt, ct),
    }


def _empty_usage() -> dict:
    return {"model": config.MODEL_NAME, "prompt_tokens": 0, "completion_tokens": 0, "cost_est": 0.0}


def _merge_usage(accum: dict, one: dict) -> dict:
    """多轮重试的 token 累计（每轮都计费）。model 取配置值。"""
    pt = accum["prompt_tokens"] + one.get("prompt_tokens", 0)
    ct = accum["completion_tokens"] + one.get("completion_tokens", 0)
    return {
        "model": config.MODEL_NAME,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cost_est": _estimate_cost(pt, ct),
    }


# ---- 真实 LLM 调用（httpx 直连 OpenAI 兼容协议）----
def _call_llm(system_msg: str, user_msg: str) -> tuple[str, dict]:
    """调 /chat/completions，强制 JSON mode。返回 (content, usage)；失败抛异常。"""
    url = f"{config.OPENAI_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},  # 强制 JSON mode
        "max_tokens": config.LLM_MAX_TOKENS,
    }
    with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    usage = _usage_from_response(data.get("usage") or {})
    return content, usage


def _parse_and_validate(content: str, schema_cls: type[T]) -> T:
    """JSON 解析 + Pydantic 校验；失败抛 ValueError 供重试逻辑捕获。"""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 返回不是合法 JSON：{exc}") from exc
    try:
        return schema_cls.model_validate(obj)
    except ValidationError as exc:
        raise ValueError(f"校验失败：{exc}") from exc


# ---- 生成主入口 ----
def generate(
    platform: str,
    schema_cls: type[T],
    system_msg: str,
    user_msg: str,
    check_sensitive: bool = True,
) -> GenerationResult:
    """生成一篇结构化内容。

    mock 模式直接返回固定 JSON；真实模式最多 3 轮（1 首发 + 2 重试），重试时把上一轮
    错误信息追加进 user 消息。成文后过敏感词，命中即 failed（非重试类错误）。
    敏感词过滤对两条路径统一生效——mock 也是成文，照样过闸。
    check_sensitive=False 仅限非发布产物（如周度拆解的模式总结），是唯一豁免口。
    """
    # ---- 取得成文（mock 或真实 LLM）----
    if config.LLM_MOCK:
        article: T | None = _mock_article(schema_cls)
        usage = _mock_usage()
        logger.info("mock 降级生成完成（platform=%s）", platform)
    else:
        article, usage, gen_error = _real_generate(platform, schema_cls, system_msg, user_msg)

    if article is None:
        # 真实路径 3 轮仍失败：落 failed 行，usage 仍记账（已消耗的 token）
        return GenerationResult(article=None, usage=usage, error=gen_error or "生成失败")

    # ---- 成文后过敏感词（命中即 failed，非重试类，SDD 8.1；两条路径统一）----
    if check_sensitive:
        full_text = " ".join(str(v) for v in article.model_dump().values())
        hits = sensitive.find_hits(full_text, platform)
        if hits:
            hit_str = "、".join(hits)
            logger.warning("敏感词命中（platform=%s）：%s", platform, hit_str)
            return GenerationResult(
                article=article,
                usage=usage,
                error=f"命中敏感词：{hit_str}",
                sensitive_hits=hits,
            )

    return GenerationResult(article=article, usage=usage, error=None)


def _real_generate(platform, schema_cls, system_msg, user_msg) -> tuple[T | None, dict, str | None]:
    """真实 LLM 路径：最多 3 轮（首发 + 2 重试），重试追加错误信息。

    返回 (article, usage, error)：失败时 article=None，usage 仍累计已消耗
    token（每轮都计费），error 供调用方写 articles.error。
    4xx（429 除外）是请求本身的问题，重试不会好——直接放弃不烧重试。
    """
    accum_usage = _empty_usage()
    last_error: str | None = None
    article: T | None = None

    for attempt in range(1, config.LLM_MAX_RETRIES + 2):  # 1..N+1（首发 + N 次重试）
        user = user_msg
        if last_error:
            # 把上一轮错误追加进提示，要求模型修正（计划书 M5 / SDD 8.1）
            user = (
                f"{user_msg}\n\n"
                f"上一次输出存在问题：{last_error}\n"
                "请修正问题，严格按要求的 JSON 格式重新输出，不要输出任何其他内容。"
            )
        try:
            content, usage = _call_llm(system_msg, user)
            accum_usage = _merge_usage(accum_usage, usage)
            article = _parse_and_validate(content, schema_cls)
            last_error = None
            break  # 校验通过
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            last_error = f"HTTP {status_code}: {exc.response.text[:200]}"
            if 400 <= status_code < 500 and status_code != 429:
                logger.error(
                    "生成失败（platform=%s，第 %d 轮，4xx 不可重试）：%s",
                    platform, attempt, last_error,
                )
                break
            logger.warning("生成第 %d 轮失败（platform=%s）：%s", attempt, platform, last_error)
        except Exception as exc:  # 网络错误 / JSON 错误 / 校验错误
            last_error = repr(exc) if not str(exc) else str(exc)
            logger.warning("生成第 %d 轮失败（platform=%s）：%s", attempt, platform, last_error)
        if attempt <= config.LLM_MAX_RETRIES:
            # 固定退避：同一轮内别把重试打满，给上游一点恢复时间
            time.sleep(config.LLM_RETRY_BACKOFF_SECONDS)

    return article, accum_usage, last_error
