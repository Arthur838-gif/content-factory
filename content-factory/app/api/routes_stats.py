"""P4 数据飞轮报表接口：成本、模板效果分、阈值校准视图（全为确定性计算）。"""
from fastapi import APIRouter, HTTPException, Query

from ..services import scoring

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats/cost")
def stats_cost(
    month: str | None = Query(None, description="YYYY-MM，缺省当月"),
) -> dict:
    """月度成本报表：双端 tokens / 文章数 / cost_est 合计 + xhs 平均单篇成本。

    估算口径：cost_est 按配置单价折算，单价未按当前供应商修正前不作账单依据。
    """
    if month is not None and (len(month) != 7 or month[4] != "-"):
        raise HTTPException(status_code=422, detail="month 格式应为 YYYY-MM")
    return scoring.cost_report(month)


@router.get("/stats/cost/article/{article_id}")
def stats_article_cost(article_id: int) -> dict:
    """单篇生成成本明细（该 article 的 meta.usage）。"""
    result = scoring.article_cost(article_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"article {article_id} 不存在")
    return result


@router.get("/prompts/stats")
def stats_prompts() -> list[dict]:
    """模板效果分（派生报表，不落字段）：按 prompt 版本聚合已发布文章互动均值。

    published < PROMPT_STATS_MIN_SAMPLES 时 sufficient_samples=false，只展示。
    """
    return scoring.prompt_stats()


@router.get("/stats/threshold-calibration")
def stats_threshold_calibration() -> dict:
    """阈值校准视图：viral_samples 判定 × 实际发布效果交叉表 + 当前阈值。

    只提供对照数据；校准结论由周四校准会人工拍板，系统不自动改写阈值。
    """
    return scoring.calibration_view()
