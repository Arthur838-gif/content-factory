# P4 · 数据飞轮 — 任务四件套

> 阶段：P4（开发计划书第 9 章路线图第 7 阶段，七阶段路线的最后一个）
> 预估：1 天 · 范围：publish_records 回填驱动的评分、阈值校准与成本报表
> 前置：P-1b 已完成并通过验收（viral_samples 数据底座、可热改阈值就位）；P3 发布回填 API 与表单就位
> 编码模型建议：DeepSeek V4 Pro（计划书 13.2 分档：回填与评分逻辑简单）
> ⚠️ 本文档与代码实现解耦：开工前需先确认 P-1b 真实环境验收（3 天采样）与 P3 两分钟人工发布已完成，否则 P4 只能以合成数据做结构验收，质量验收顺延。

## 【目标】

完成数据飞轮（P4），让"发布 → 回填 → 评分 → 反哺选题与模板"闭环跑通。具体交付：

1. **发布回填闭环**：P3 已交付 `POST /api/articles/{id}/publish` 接口与表单（落 publish_records、article → published）。P4 补齐其**副作用**：回填成功后触发 `topics.score` 与模板效果分计算，使回填数据真正反哺系统，而不是只进不出。
2. **`topics.score` 评分**：按回填数据（阅读/点赞/收藏/评论）重算选题综合评分；`GET /api/topics` 与选题台 `/` 按 score 倒序即体现回填效果。**计划书与 SDD 均未定义公式**，本阶段必须先拍板公式（见"已拍板的契约决策"），先写进本文档附录再实现。
3. **模板效果分**：按回填数据统计每个 Prompt 版本（platform+scenario+version）产出的文章效果，支撑后续模板优胜劣汰。**prompts 表无现成分数字段**，按合同纪律：先在计划书第 5 章补字段定义并升版本号，再改表；本阶段若不改表，则效果分以**派生报表**形式输出（API 实时聚合，不落字段）——推荐后者，避免在数据量不足时引入写放大。
4. **低粉爆款阈值校准**：用 P-1b 积累的 viral_samples + 自己账号的 publish_records 回填数据对照，校准 `VIRAL_MAX_FANS / VIRAL_LIKES_MIN / VIRAL_SCORE_MIN` 三个阈值与撞题 `TOPIC_DUPLICATE_JACCARD` 阈值。校准结论两处硬产出：**写回 config（可热改）+ 记录到文档附录**。
5. **成本报表**：汇总 `articles.meta.usage`（prompt_tokens / completion_tokens / model / cost_est），输出：
   - 单篇成本：指定 article 的生成成本明细；
   - 月度成本：按月聚合的双端成本统计；
   - 必须能回答验收问句"**一篇小红书笔记平均生成成本是多少**"。

**P4 不做的事**：P5 内容查重、P6 封面 A/B 测试、P7 Embedding 聚类、docker compose 打包、部署形态决策（本地/VPS，计划书第 4、12 章明确留 P4 之后）、自动发布与自动互动（全局非目标，回填永远由人工发起）。

## 【上下文】

- 项目根目录：`C:\YU\project\文案工厂\content-factory`
- 请先阅读：
  - `app/models.py`（`PublishRecord / Topic / Article / Prompt / ViralSample / HotItem` 字段，P4 不改记账逻辑）
  - `app/api/routes_articles.py`（P3 已实现的 publish 回填接口与 ZIP）
  - `app/api/routes_topics.py`（topics 列表排序现状）
  - `app/services/radar.py`（P-1b 阈值使用点，校准后 config 新值须即刻生效——读取而非缓存）
  - `app/config.py`（`VIRAL_*` 阈值、`CF_LLM_PRICE_INPUT/OUTPUT` 单价配置）
  - `app/services/generator.py`（`meta.usage` 记账格式，P4 只汇总不修改）
  - `app/templates/`（选题台与文章预览页，回填表单已存在；报表页沿用 Jinja2 + 原生 JS）
  - 开发计划书第 5 章数据模型（publish_records / meta.usage 约定）/ 第 6.2 节阈值"待确认"标注 / 第 8 章 API / 第 9 章 P4 段 / 第 14 章迭代项
  - SDD 4.2 publish 接口契约与副作用 / 5.5 阈值 Hypothesis 标注

### 已拍板的契约决策

1. **publish_records 只增不改，article_id 不漂移**（计划书第 5 章）：article_id 永远指向发布时那一行；该 article 后续若被归档重生成，回填数据仍挂在原行上。评分按"发布时的那一行"归因，不追随最新行。
2. **评分是幂等重算，不是增量累加**：`topics.score` 与模板效果分任何时候都可从 publish_records 全量重算得出；重算服务（如 `app/services/scoring.py`）提供 `recompute()`，publish 回填成功后调用一次，也可手动全量重算。这样回填补录（P3 契约允许 metrics 后续补）只需重算，无需对账逻辑。
3. **topics.score 建议公式（开工时确认，先写附录再实现）**：
   `score = 基础分 + 效果分`。基础分沿用现状（radar 选题的 viral_score 归一、manual 选题的人工分）；效果分 = 该 topic 所有已发布文章的归一化互动 `Σ(likes + 2×collects + 3×comments)` 的对数压缩（如 `log1p`），避免单篇爆文压制全部排序。无回填数据的选题 score 不变——**不允许回填机制拉低未发布选题的排序公平性**。
4. **模板效果分推荐派生报表**：`GET /api/prompts/stats` 实时按 `prompt_id`（articles 需可追溯到生成所用 prompt 版本——若 articles 未记录 prompt_id，须在 P4 开工第 0 步确认并补 meta 记录，**不改表结构**）聚合：`published_count / avg_likes / avg_collects / avg_comments`。数据量 < 10 篇时不做任何自动启停建议，只展示。
5. **阈值校准是人工结论，不是自动调参**：P4 提供对照数据视图（viral_samples 判定结果 × 实际发布效果），校准值由人工在周四校准会（计划书第 14 章运营节奏）拍板后改 config；系统不自动改写阈值。
6. **成本口径**：成本报表以 `meta.usage.cost_est` 为准；GLM 真实单价必须通过 `CF_LLM_PRICE_INPUT / CF_LLM_PRICE_OUTPUT` 配置修正（当前默认 DeepSeek 单价，GLM 验收报告已标注 cost_est 不可作账单依据）。P4 开工前必须先校准单价配置，否则成本报表无效。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `publish_records / articles / topics / prompts / viral_samples / hot_items`。

- `publish_records`：`article_id / platform / account / url / metrics / published_at`；只增不改，永久保留。P4 首次成为**读取方**（此前 P3 只写）。
- `articles.meta.usage`：`{"prompt_tokens","completion_tokens","model","cost_est"}` 格式不变；P4 只汇总。
- `articles.meta` 增补约定（不改表）：生成落库时记录所用 `prompt_id` 与 `prompt_version`，供模板效果分归因；已在 P0-P1 生成的历史文章无此字段，效果分统计时归为"未知版本"组。
- `topics.score`：由重算服务写回；写回只升不降的约束不强制（重算结果是什么就是什么），但必须在 evidence 或服务日志中可追溯到本次重算的回填数据版本。
- **不改表结构**：若实现中确需 prompts 效果分字段或 collector_state 式的新表，必须先改计划书第 5 章并升版本号（合同纪律），再开工。

## 【验收标准】

计划书 P4 原文：**回填一篇已发内容的数据后，选题列表可按 score 排序；阈值校准结论写回 config 并记录到文档附录；成本报表可回答"一篇小红书笔记平均生成成本是多少"。** 展开为可执行条目：

### 结构验收（合成数据，不依赖真实发布）

1. **回填触发评分**：造 2 篇同 topic 文章 + 3 篇不同 topic 文章，对其中 2 篇 POST publish 回填（不同 likes/collects/comments）后：
   - publish_records 行数正确、只增不改；
   - article → published；
   - `GET /api/topics` 按 score 倒序，回填过高效互动的 topic 排前；
   - 无回填的 topic score 与回填前一致。
2. **重算幂等**：连续调用 `recompute()` 两次，topics.score 结果完全一致；补录一条 metrics 后重算，score 按新数据更新。
3. **模板效果分**：`GET /api/prompts/stats` 按 prompt 版本聚合 published 文章的互动均值；无 meta.prompt_id 的历史文章归入"未知版本"组且不报错。
4. **成本报表**：
   - `GET /api/stats/cost?month=YYYY-MM` 返回该月双端 prompt/completion tokens、文章数、cost_est 合计；
   - xhs 平均单篇成本 = 当月 xhs cost_est 合计 ÷ xhs 文章数，接口或报表页直接给出该数字；
   - 报表页 `/stats`（或选题台入口）可视化展示，沿用 Jinja2，无前端框架。
5. **阈值校准视图**：`GET /api/stats/threshold-calibration`（或报表页区块）展示 viral_samples 判定结果与对应发布效果的交叉表；人工结论写回 config 后，radar 立即按新阈值判定（不重启）。
6. **文档附录**：`docs/p4-calibration.md`（或计划书附录）记录校准日期、数据样本量、旧值、新值、理由。
7. `tests/test_p4.py` 可重复运行且通过；P0 / P1 / P1a / P2 / P3 / P-1b 回归全部通过。

### 质量验收（真实数据，前置：P3 人工发布与 P-1b 三天采样已完成）

1. 真实回填至少 1 篇已发布小红书笔记的 metrics，确认选题台排序变化符合公式预期。
2. 成本报表基于修正后的 GLM 单价，回答"一篇小红书笔记平均生成成本"，数字与 `meta.usage` 手工抽算一致。
3. 周四校准会用真实对照数据完成一次三阈值评审（改或不改都要记录结论到文档附录）。

## 【纪律】

- 不实现计划书第 2 章非目标；回填永远人工发起，不做任何自动抓取发布数据的爬虫/浏览器自动化。
- publish_records 只增不改；任何"修正数据"需求通过新增一行实现，严禁 UPDATE。
- 评分公式、权重系数、对数压缩参数全部集中 `config.py` 或 YAML 配置，写进代码即返工。
- 成本报表依赖真实单价：`CF_LLM_PRICE_INPUT / CF_LLM_PRICE_OUTPUT` 未按 GLM 官方价修正前，报表必须标注"估算口径"，不得冒充账单。
- 不自动启停 Prompt 模板、不自动改写阈值；数据量不足（published < 10 篇）时一切结论仅供参考。
- 依赖克制：重算与报表用 SQLAlchemy 聚合 + Jinja2，不引 pandas/前端框架/新依赖。
- docker compose、部署形态、鉴权、HTTPS 均不在本阶段（计划书明确留 P4 之后）。
- 每个接口与重算逻辑附最小验证进 `tests/test_p4.py`，本地跑过才算完成。

## 【无真实数据降级方案】

- 真实发布数据不足时，结构验收全部使用合成 publish_records（测试内构造，不入正式库）；质量验收顺延，在 README 记录缺口。
- P-1b 真实采样未完成（fans 探针降级中）时，阈值校准视图仍可实现，但校准结论标注"样本不足，维持初值"。
- LLM 不参与 P4 任何链路（评分、报表、校准视图全是确定性计算），无需 mock 分支。

## 【交付文件清单】

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `app/services/scoring.py` | 新建 | 重算服务：topics.score 与模板效果分全量幂等重算；公式参数从 config 读 |
| `app/api/routes_articles.py` | 修改 | publish 回填成功后触发 scoring.recompute() |
| `app/api/routes_stats.py` | 新建 | `GET /api/stats/cost`、`GET /api/prompts/stats`、`GET /api/stats/threshold-calibration` |
| `app/api/routes_pages.py` + `app/templates/stats.html` | 修改/新建 | 报表页：成本统计、模板效果、校准视图入口 |
| `app/api/routes_topics.py` | 修改/确认 | 列表按 score 倒序（若已如此则仅补测试） |
| `app/services/generator.py` 或落库处 | 修改 | articles.meta 记录 prompt_id / prompt_version（不改表） |
| `app/config.py` | 修改 | 评分权重、对数压缩参数、GLM 单价配置项确认 |
| `docs/p4-calibration.md` | 新建 | 阈值校准附录：日期、样本量、旧值、新值、理由（首次可为"维持初值"） |
| `tests/test_p4.py` | 新建 | 结构验收 1–7（合成数据端到端 + 幂等 + 回归） |
| `README.md` | 修改 | 进度更新为 P4；报表页与校准流程说明 |

## 【验收脚本示意】

```bash
# 1. 造数据：生成两篇 xhs（mock），回填不同互动量
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/2/generate?platform=xhs"
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/articles/1/publish" \
  -H "Content-Type: application/json" \
  -d '{"platform":"xhs","account":"主号","url":"https://www.xiaohongshu.com/explore/a","metrics":{"likes":320,"collects":88,"comments":21}}'
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/articles/2/publish" \
  -H "Content-Type: application/json" \
  -d '{"platform":"xhs","account":"主号","url":"https://www.xiaohongshu.com/explore/b","metrics":{"likes":12,"collects":3,"comments":1}}'

# 2. 选题排序反映回填效果
curl --noproxy '*' -s "http://127.0.0.1:8000/api/topics" | python -c "import sys,json; [print(t['id'],t['score']) for t in json.load(sys.stdin)]"

# 3. 成本报表：回答"一篇小红书笔记平均生成成本"
curl --noproxy '*' -s "http://127.0.0.1:8000/api/stats/cost?month=$(date +%Y-%m)" | python -m json.tool

# 4. 模板效果分
curl --noproxy '*' -s "http://127.0.0.1:8000/api/prompts/stats" | python -m json.tool

# 5. 阈值校准视图 → 人工结论 → 改 config（环境变量）→ 不重启生效
curl --noproxy '*' -s "http://127.0.0.1:8000/api/stats/threshold-calibration" | python -m json.tool
export CF_VIRAL_SCORE_MIN=1.8   # 示例：校准会后的人工结论
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_sample/run"   # 新阈值即刻生效

# 6. 回归
.venv/Scripts/python tests/test_p4.py
```

## 【报表页信息架构（实现时可微调，信息密度不得减少）】

- `/stats` 报表页三区：
  - 成本区：本月双端 tokens / 文章数 / cost_est 合计 / xhs 平均单篇成本（大字突出）/ 历史月份小表；
  - 模板效果区：按 platform+scenario 分组的版本表（published_count、平均点赞/收藏/评论），数据量不足标"样本不足"；
  - 阈值校准区：viral_samples 判定 × 发布效果交叉表 + 当前阈值展示 + "修改方式：环境变量"提示（页面不提供写阈值功能，防止绕过校准会纪律）。

## 【P4 之后的明确延期项（不在本阶段开工）】

- P5 内容查重（启动条件：P3 交付后、第一篇正式发布前；注意此条件实际已成熟，可独立于 P4 排期）。
- P6 封面 A/B 测试（启动条件：累计发布 ≥ 10 篇、回填数据稳定——正是 P4 机制的产出）。
- P7 Embedding 聚类（启动条件：topics ≥ 1000）。
- 部署形态决策与 docker compose 打包。
- 运营节奏机制化（周一选题会、周四校准会）从 P4 交付后开始执行。
