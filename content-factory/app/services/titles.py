"""标题打分（融合红狐 xiaohongshu-title-score 方法论：六维加权 + S/A/B/C 等级）。

与红狐原版的差异：不打红狐数据接口（付费），规律内化在提示词里由 LLM 评审；
纯无状态服务，不落库（打分不是发布产物）。成本约每次百级 token。
"""
import json
import logging

from .. import config
from . import generator, prompt_engine

logger = logging.getLogger(__name__)

_DIMENSIONS = ("主题匹配度", "结构合规度", "利益清晰度", "情绪唤醒度", "稀缺性感知", "合规安全性")

# mock：按维度均值 + 等级推断，结构与真实路径完全一致（测试/UI 联调用）
_MOCK = {
    "total": 7.4,
    "grade": "A",
    "dimensions": [
        {"name": n, "score": 7.4, "weight": w, "comment": "mock 评分（LLM_MOCK）"}
        for n, w in zip(_DIMENSIONS, ("15%", "20%", "25%", "20%", "15%", "5%"))
    ],
    "problems": ["mock：情绪词偏弱"],
    "suggestions": ["mock：补一个具体数字", "mock：加利益承诺（看完能得到什么）"],
    "revised_titles": ["mock 干货风标题", "mock 情绪风标题"],
}


def _grade(total: float) -> str:
    if total >= 9.0:
        return "S"
    if total >= 7.0:
        return "A"
    if total >= 5.0:
        return "B"
    return "C"


def score(title: str, keyword: str = "") -> dict:
    """给一个小红书标题打分。返回 {total, grade, dimensions, problems,
    suggestions, revised_titles}；LLM 失败抛 RuntimeError 由端点转 502。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if config.LLM_MOCK:
        return dict(_MOCK)
    from ..db import session_scope

    with session_scope() as session:
        _prompt, system_msg, user_msg = prompt_engine.render_messages(
            session, "xhs", "title_score", {"title": title, "keyword": keyword.strip()}
        )
    raw, _usage = generator._call_llm(system_msg, user_msg)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"打分返回不是合法 JSON：{exc}") from exc
    total = round(float(obj.get("total") or 0), 1)
    dims = [
        {
            "name": str(d.get("name", "")),
            "score": round(float(d.get("score") or 0), 1),
            "weight": str(d.get("weight", "")),
            "comment": str(d.get("comment", "")),
        }
        for d in obj.get("dimensions") or []
        if isinstance(d, dict)
    ]
    return {
        "total": total,
        # 等级按总分确定性推导（LLM 自报等级偶尔与总分打架，不信它）
        "grade": _grade(total),
        "dimensions": dims,
        "problems": [str(p) for p in obj.get("problems") or []],
        "suggestions": [str(s) for s in obj.get("suggestions") or []],
        "revised_titles": [str(t) for t in obj.get("revised_titles") or []],
    }
