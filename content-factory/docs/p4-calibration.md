# P4 阈值校准与评分公式附录

本文件是 P4 数据飞轮的两项硬产出落点：

1. **topics.score 评分公式**（计划书与 SDD 均未定义，P4 开工前在此拍板，实现以此为准）；
2. **低粉爆款阈值校准记录**（周四校准会人工结论，改或不改都要记录）。

## 一、topics.score 评分公式（2026-08-18 拍板）

```
score = base_score + effect_score
effect_score = SCORE_EFFECT_SCALE × log1p( Σ (likes×W_L + collects×W_C + comments×W_M) )
```

- **求和范围**：该 topic 下所有 `status=published` 文章关联的 `publish_records.metrics`
  （含补录行；评分按"发布时那一行"归因，article_id 不漂移）。
- **base_score（基础分）**：选题落库时的原始评分——radar 选题 = 采样时的 viral_score /
  热榜基线分，manual 选题 = 人工分。首次重算时快照进 `topics.evidence["base_score"]`，
  此后不再变化，保证幂等重算可复现。
- **对数压缩**：`log1p`（ln(1+x)），避免单篇爆文压制全部排序。
- **权重参数**（集中在 `config.py`，环境变量可改，写进代码即返工）：

| 参数 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| W_L | `CF_SCORE_W_LIKES` | 1 | 点赞权重（与 viral_score 同构） |
| W_C | `CF_SCORE_W_COLLECTS` | 2 | 收藏权重 |
| W_M | `CF_SCORE_W_COMMENTS` | 3 | 评论权重 |
| SCORE_EFFECT_SCALE | `CF_SCORE_EFFECT_SCALE` | 1.0 | 效果分整体缩放 |

- **公平性约束**：无回填数据的选题 `effect_score=0`，score 等于 base_score，
  回填机制不拉低未发布选题的排序。
- **幂等**：评分是全量重算而非增量累加；`scoring.recompute()` 任何时候重跑结果一致，
  补录 metrics 后重跑即按新数据更新。
- **可追溯**：每次重算在 `topics.evidence["score_recompute"]` 记录本次归因到的
  `publish_record_max_id`、互动合计与时间戳。

## 二、阈值校准记录

校准对象：`VIRAL_FANS_MAX / VIRAL_LIKES_MIN / VIRAL_SCORE_MIN`（第 6.2 节）与
撞题 `TOPIC_DUPLICATE_JACCARD`。流程：周四校准会对照
`GET /api/stats/threshold-calibration`（viral_samples 判定 × 实际发布效果交叉表）
人工拍板 → 改环境变量（config）→ 记录到本表。系统不自动改写阈值。

| 日期 | 阈值 | 旧值 | 新值 | 数据样本量 | 结论与理由 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-18 | VIRAL_FANS_MAX | 5000 | 5000（维持） | 0（P-1b 采样降级中，无对照数据） | 样本不足，维持初值；待真实采样 + 自有账号发布对照数据积累后评审 |
| 2026-08-18 | VIRAL_LIKES_MIN | 500 | 500（维持） | 0（同上） | 样本不足，维持初值 |
| 2026-08-18 | VIRAL_SCORE_MIN | 2.0 | 2.0（维持） | 0（同上） | 样本不足，维持初值 |
| 2026-08-18 | TOPIC_DUPLICATE_JACCARD | 0.5 | 0.5（维持） | 0（同上） | 样本不足，维持初值 |
