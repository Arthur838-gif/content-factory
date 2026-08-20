"""标题打分（融合红狐方法论：小红书六维 / 公众号四维加权 + S/A/B/C 等级）。

与红狐原版的差异：不打红狐数据接口（付费），规律内化在提示词里由 LLM 评审；
纯无状态服务，不落库（打分不是发布产物）。成本约每次百级 token。
P9：platform 参数分流——xhs 六维（0-10 制）/ wechat 四维（0-100 制），
响应 JSON 形状两平台一致，前端零适配。
"""
import json
import logging

from . import generator, model_config, prompt_engine

logger = logging.getLogger(__name__)

_XHS_DIMENSIONS = ("主题匹配度", "结构合规度", "利益清晰度", "情绪唤醒度", "稀缺性感知", "合规安全性")
_GZH_DIMENSIONS = ("赛道匹配度", "点击诱因强度", "结构合规性", "爆文潜质匹配度")

# 各平台 mock：结构与真实路径完全一致（测试/UI 联调用）
_MOCKS = {
    "xhs": {
        "total": 7.4,
        "grade": "A",
        "dimensions": [
            {"name": n, "score": 7.4, "weight": w, "comment": "mock 评分（LLM_MOCK）"}
            for n, w in zip(_XHS_DIMENSIONS, ("15%", "20%", "25%", "20%", "15%", "5%"))
        ],
        "problems": ["mock：情绪词偏弱"],
        "suggestions": ["mock：补一个具体数字", "mock：加利益承诺（看完能得到什么）"],
        "revised_titles": ["mock 干货风标题", "mock 情绪风标题"],
    },
    "wechat": {
        "total": 76.5,
        "grade": "B",
        "dimensions": [
            {"name": n, "score": 7.7, "weight": w, "comment": "mock 评分（LLM_MOCK）"}
            for n, w in zip(_GZH_DIMENSIONS, ("15%", "35%", "15%", "35%"))
        ],
        "problems": ["mock：点击诱因偏弱"],
        "suggestions": ["mock：把利益点前置到标题前 10 字", "mock：补具体数字增强可信度"],
        "revised_titles": ["mock 悬念风标题", "mock 干货清单风标题", "mock 人群代入风标题"],
    },
}


def _grade(platform: str, total: float) -> str:
    # 等级按总分确定性推导（LLM 自报等级偶尔与总分打架，不信它）；
    # xhs 六维是 0-10 制，wechat 四维映射到 0-100 制，阈值各表
    thresholds = (90.0, 70.0, 50.0) if platform == "wechat" else (9.0, 7.0, 5.0)
    if total >= thresholds[0]:
        return "S"
    if total >= thresholds[1]:
        return "A"
    if total >= thresholds[2]:
        return "B"
    return "C"


def score(title: str, keyword: str = "", platform: str = "xhs") -> dict:
    """给标题打分（platform：xhs 六维 / wechat 四维，均融合红狐方法论）。

    返回 {total, grade, dimensions, problems, suggestions, revised_titles}；
    平台不支持或标题为空抛 ValueError（端点转 422）；
    LLM 失败抛 RuntimeError 由端点转 502。
    """
    if platform not in _MOCKS:
        raise ValueError(f"暂不支持平台 {platform}（支持 xhs / wechat）")
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if model_config.mock_enabled(model_config.PURPOSE_TEXT):
        return dict(_MOCKS[platform])
    from ..db import session_scope

    with session_scope() as session:
        _prompt, system_msg, user_msg = prompt_engine.render_messages(
            session, platform, "title_score", {"title": title, "keyword": keyword.strip()}
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
        "grade": _grade(platform, total),
        "dimensions": dims,
        "problems": [str(p) for p in obj.get("problems") or []],
        "suggestions": [str(s) for s in obj.get("suggestions") or []],
        "revised_titles": [str(t) for t in obj.get("revised_titles") or []],
    }
