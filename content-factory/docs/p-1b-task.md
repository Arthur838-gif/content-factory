# P-1b · 低粉爆款引擎 — 任务四件套

> 阶段：P-1b（开发计划书第 9 章路线图第 6 阶段，P3 与 P4 之间）
> 预估：1 天 · 模块：M2 小红书采样器 + M3 选题雷达分析
> 前置：P3 已完成并通过验收（素材包、预览页、发布回填 API 就位）；P-1a 的 `hot_items / viral_samples / tag_library` 表、调度、告警通道与 Docker 前置检查已就位

## 【目标】

完成低粉爆款引擎（P-1b），范围仅限 **M2 小红书采样器 + M3 选题雷达分析**，其他模块不实现。具体交付：

1. **第 0 步 · fans 字段探针（≤1 小时）**：先实测 xiaohongshu-mcp 搜索结果是否返回作者 `fans` 字段。结论必须先写进编码会话上下文与交付记录，再决定自动采样路径：
   - 有 `fans`：自动采样可进入低粉爆款计算。
   - 无 `fans`：自动采样器只落笔记级数据，低粉爆款进入“人工喂样本”降级模式；降级不改变后续打分管线。
2. **xiaohongshu-mcp 本地部署**：按 xpzouying/xiaohongshu-mcp 的 Go 独立服务方式，用 Docker 起在本机，默认地址 `http://localhost:18060`；使用专用数据小号扫码登录。部署脚本/步骤只写文档或仓库外操作说明，不把账号 Cookie、二维码截图或凭据写入仓库。
3. **M2 采样器 `app/services/collectors/xhs_sample.py`**：遵循采集统一协议 `fetch() -> list[HotItem]`；只调用搜索/推荐流等只读接口；按 URL 去重；按 `data/domains.yml` 领域关键词过滤；写 `hot_items`。
4. **M3 低粉爆款打分 `app/services/radar.py`**：实时打分只做规则计算，不调 LLM；公式为 `viral_score = (likes + 2 × collects + 3 × comments) ÷ max(fans, 1)`。初值阈值：`fans ≤ 5000`、`likes ≥ 500`、`viral_score ≥ 2.0`，三者集中 `config.py` 可热改。
5. **自动产出雷达选题**：判定为低粉爆款后写 `viral_samples`，并自动生成一条 `topics`（`source = radar`、`status = new`、`evidence` 存笔记关键数据快照）。
6. **撞题去重**：自动建 topic 前查近 7 天 `status != archived` 且未过期 topics；标题分词后 Jaccard ≥ 0.5 视为同一选题，不新建，样本链接追加到已有 `evidence`，`score` 取较大值；< 0.5 才新建。
7. **人工喂样本降级入口**：当 fans 不可用或自动采样不可用时，管理页/API 可录入笔记链接、作者、粉丝数、点赞、收藏、评论与领域；录入后走与自动样本完全相同的打分、落库、撞题与建题管线。
8. **周度 LLM 拆解任务**：使用附录 A3 模板（`platform=xhs, scenario=teardown`），每周一次把当周 `viral_samples` 交 LLM 总结标题模式、情绪词与结构套路；结论写回 `viral_samples.reason` 与 `tag_library`。周度拆解由调度器触发；手动触发接口仅用于调试。
9. **熔断与告警**：M2 连续失败 3 次自动熔断该采集器；熔断状态在管理页/状态接口可见，并经 `NOTIFY_WEBHOOK` 外发；熔断后只能人工恢复，不自动重试。

**P-1b 不做的事**：小红书自动发布或自动互动（关注、点赞、评论、私信、回关）、AI 生图、多账号矩阵、IP 池、设备指纹、SaaS 化、移动端、视频、P4 的 publish_records 评分/成本报表/阈值校准分析、Embedding/向量聚类、docker compose 生产部署定型。

## 【上下文】

- 项目根目录：`C:\YU\project\文案工厂\content-factory`
- 请先阅读：
  - `app/models.py`（`HotItem / ViralSample / TagLibrary / Topic` 合同，P-1a 已建，本阶段不改表）
  - `app/config.py`（调度、告警、数据目录、LLM 配置约定；本阶段新增 mcp 地址与阈值）
  - `app/services/collectors/`（既有采集器统一协议与注册方式）
  - `app/services/radar.py`（P-1a 雷达分析骨架，本阶段扩展低粉爆款打分、撞题与自动建题）
  - `app/api/routes_admin.py`（调度任务、采集器手动触发与状态接口风格）
  - `app/api/routes_pages.py` / `app/templates/`（管理页信息架构；人工喂样本入口保持 Jinja2 + 原生 JS）
  - `app/services/prompt_engine.py` / `app/services/generator.py`（A3 模板入库与周度拆解调用方式）
  - `data/domains.yml`（赛道关键词清单）
  - 开发计划书第 2 章非目标 / 第 5 章数据模型 / 第 6.2 节低粉爆款引擎 / 第 7 章 M2、M3 规格 / 第 8 章 API / 第 9 章 P-1b / 第 11 章风险 / 第 12 章前置检查 / 第 14 章后续迭代 / 附录 A3
  - SDD 3.2 M2、M3 边界 / 5.1 数据模型 / 5.2 状态流转 / 风险与熔断章节

### 已拍板的契约决策

1. **只读采样**：M2 只允许搜索、推荐流读取；禁止任何写接口、互动接口和账号行为。数据小号必须与发布账号隔离，凭据不进 Git、不进聊天回复、不进测试快照。
2. **降级同管线**：fans 不可用时，自动采样仍写 `hot_items` 笔记级数据；人工喂样本补齐 fans/likes/collects/comments 后进入同一条 M3 打分、落库、撞题、建题管线。降级模式下 P-1b 不算失败。
3. **阈值是配置，不是代码**：`VIRAL_MAX_FANS=5000`、`VIRAL_MIN_LIKES=500`、`VIRAL_MIN_SCORE=2.0`、`TOPIC_DUPLICATE_JACCARD=0.5` 等初值集中 `config.py`，支持环境变量热改；校准结论推迟到 P4。
4. **实时不调 LLM**：每次采样的低粉爆款判定只做确定性规则计算；LLM 仅用于周度拆解，避免把日采样成本与不稳定性引入实时链路。
5. **发现不决策**：M3 自动产出选题只进入 `topics.status = new`；是否生成仍由人工在选题台点击。
6. **熔断可恢复但不自愈**：失败计数与熔断状态持久化；连续 3 次失败后暂停 M2，告警一次，人工检查并显式恢复后清零计数。
7. **聚类不上向量**：撞题判定只使用中文标题分词/字符 n-gram 与 Jaccard 重叠度；Embedding 替换属于后续迭代，替换前必须做 golden set 回归对比。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `hot_items / viral_samples / tag_library / topics / prompts`，**不改表结构**。

- `hot_items`：`source=xhs`、`title`、`url`（唯一去重键）、`author`、`fans`、`likes`、`collects`、`comments`、`cluster`、`raw`（原始 JSON 快照）、`captured_at`。`fans` 无法自动获取时允许为空；人工样本必须显式填写。
- `viral_samples`：`hot_item_id`、`domain`、`viral_score`、`title_pattern`、`reason`、`created_at`。实时判定可先写 `title_pattern="auto"`、`reason="rule"`；周度拆解后把 LLM 结论回写到对应样本的 `reason` 与模式字段。
- `topics`：自动建题时 `source=radar`、`status=new`、`score` 取样本分与既有撞题分较大值、`evidence` 存样本 URL、作者、互动数、viral_score、命中关键词、判定时间等 JSON 快照。
- `tag_library`：`domain + tag` 唯一；周度拆解命中的标签累计 `heat`，供 P1/P2 Prompt 的可选标签参考。
- `prompts`：新增 A3 拆解种子模板，幂等键 `xhs+teardown+v1`；不覆盖库内已有同名版本。
- 保留策略：`hot_items` 只留 90 天；`viral_samples / tag_library / topics / prompts` 永久保留。清理任务必须与备份任务错开至少 1 小时。

## 【验收标准】

计划书 P-1b 原文：**连续采样 3 天（降级模式为人工录入 3 天），产出 ≥ 5 条 `viral_samples`，人工抽查过半数“确实值得仿写”；采集器失败自动暂停（熔断）机制演练通过。** 展开为可执行条目：

### 结构验收（本地可重复，不依赖真实小红书）

1. **fans 探针记录**：在 `docs/` 或验收记录中写明 xiaohongshu-mcp 搜索结果是否含 `fans`，并注明选择自动模式或降级模式的依据。
2. **M2 协议**：`xhs_sample.fetch()` 在 mock/录制响应下返回 `HotItem` 列表；同一 URL 重复出现只入库一次；不属于 `data/domains.yml` 领域关键词的条目不进入候选。
3. **M3 打分**：构造样本验证：
   - `fans=5000, likes=500, collects=0, comments=0` 时 `viral_score=0.1`，不入选。
   - `fans=100, likes=100, collects=50, comments=20` 时 `viral_score=(100+100+60)/100=2.6`，入选。
   - `fans=0` 或空值人工样本按 `max(fans, 1)` 计算，不出现除零。
4. **落库与建题**：入选样本写入 `viral_samples`，且自动生成 `topics(source=radar,status=new)`；`evidence` 含 URL、作者、互动数、viral_score 与命中关键词。
5. **撞题去重**：先建近 7 天相似 topic，再输入标题 Jaccard ≥ 0.5 的样本，确认不新建 topic、原 `evidence` 追加样本链接且 `score` 取较大值；Jaccard < 0.5 时新建。
6. **人工喂样本**：通过 API/页面录入一组 fans/likes/collects/comments，确认与自动样本走同一打分、落库、撞题、建题逻辑；缺 fans 或互动数字非法时返回 422/400，不写半成品。
7. **A3 模板与周度拆解**：`prompts` 幂等入库 `xhs+teardown+v1`；手动触发周度拆解（mock LLM）后，样本 `reason` 被更新且 `tag_library.heat` 有累计。
8. **熔断**：模拟连续 3 次 mcp 请求失败后，M2 状态为熔断、后续调用被拒绝、告警调用一次；人工恢复接口/操作后才允许再次执行。
9. **回归**：`tests/test_p1b.py` 可重复运行且通过；P0/P1/P2/P3 回归（`test_p0.py`、`test_p1.py`、`test_p2.py`、`test_p3.py`）仍通过。

### 质量验收（真实环境，需专用数据小号与 mcp 服务）

1. 自动模式：连续采样 3 天，检查 `viral_samples ≥ 5`；人工抽查至少一半样本，确认“确实值得仿写”。
2. 降级模式：若 fans 不可用，连续人工录入 3 天，检查 `viral_samples ≥ 5`；人工抽查标准同上。
3. 熔断演练：临时停止 mcp 服务或使用无效地址，连续触发 3 次后确认采集器暂停并发出告警；恢复服务后仍需人工解除熔断。
4. 真实采样期间不得执行任何关注、点赞、评论、私信、回关或发布操作。

## 【纪律】

- 不实现计划书第 2 章非目标，尤其小红书自动发布与自动互动；M2 只做只读采集。
- 数据小号与发布账号物理隔离、零共享凭据；Cookie、二维码、账号信息、WebHook 地址等秘密只进本地 `.env` 或操作员私有记录，不进 Git。
- 文案即数据：A3 拆解 Prompt、领域关键词、敏感词与阈值都在库/配置/文件，写进代码即返工。
- 依赖克制：优先复用 `httpx` 调 mcp HTTP 接口；除非计划书明确，不新增 Embedding、浏览器自动化、Cookie 注入或小红书私有 SDK 依赖。
- M3 实时链路不调 LLM；只有周度拆解可调用 LLM，并复用 `prompt_engine + generator` 的 JSON、重试、usage 记账能力。
- 管理页保持 Jinja2 + 原生 JS，不引前端框架；人工喂样本只做录入与状态展示，不变成数据分析后台。
- 服务仍只监听 `127.0.0.1`；docker compose、公网部署、鉴权与 HTTPS 属后续运维阶段，不在本阶段实现。
- 每个接口与状态转换附最小验证进 `tests/test_p1b.py`，本地跑过才算完成，不接受“应该可以”。

## 【无 mcp / 无小号降级方案】

- 无 Docker、无专用数据小号或 xiaohongshu-mcp 不可登录时，不阻塞代码验收：使用 mock/录制响应完成 M2 协议测试，以人工喂样本完成 M3 打分与建题测试。
- 无 `OPENAI_API_KEY` 或 `CF_LLM_MOCK=1` 时，周度拆解走 mock 分支，返回固定 JSON：标题模式、情绪词、结构套路、标签建议。该 mock 只验证管线结构，不作为真实质量验收依据。
- fans 探针不可用结论必须显式记录：`fans_available=false` 时，自动采样结果不得伪造 fans；低粉爆款只能通过人工录入补齐 fans 后产生。
- 降级模式仍需完成熔断演练（用无效 mcp 地址模拟失败）与 P0-P3 回归。

## 【交付文件清单】

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `app/services/collectors/xhs_sample.py` | 新建 | M2 小红书采样器：只读搜索/推荐流、URL 去重、领域过滤、失败计数接入 |
| `app/services/radar.py` | 修改 | M3：低粉爆款打分、viral_samples 落库、撞题去重、自动建题、周度拆解编排 |
| `app/services/collectors/base.py`（或既有注册文件） | 修改 | 注册 `xhs_sample`，供调度器与手动触发接口调用 |
| `app/api/routes_collectors.py` | 修改/新建 | `POST /api/collectors/{name}/run`；返回新增/去重/过滤/失败计数与熔断状态 |
| `app/api/routes_viral_samples.py` | 新建 | `GET /api/viral-samples?domain=`（按 viral_score 倒序）、`POST /api/viral-samples/manual`（人工喂样本） |
| `app/api/routes_admin.py` | 修改 | 暴露 M2 熔断状态与人工恢复操作；恢复必须显式调用 |
| `app/templates/` | 修改 | 管理页加入 viral samples 列表与人工喂样本表单；保持 Jinja2 + 原生 JS |
| `app/config.py` | 修改 | `XHS_MCP_BASE_URL`、采样间隔、VIRAL 三个阈值、撞题阈值、A3 调度周期 |
| `prompts/xhs_teardown.yml` | 新建 | 附录 A3 周度拆解模板，幂等键 `xhs+teardown+v1` |
| `app/services/prompt_engine.py` | 修改 | `SEED_FILES` 加 `xhs_teardown.yml` |
| `data/domains.yml` | 修改/确认 | 明确赛道关键词与标签候选；若已有则仅补充说明，不硬改业务数据 |
| `tests/test_p1b.py` | 新建 | 结构验收 1–9：协议、打分、落库、撞题、人工样本、A3 mock、熔断、回归 |
| `README.md` | 修改 | 进度更新为 P-1b；记录 fans 探针结论、降级模式与真实 3 天验收方式 |

## 【验收脚本示意】

```bash
# 0. fans 字段探针（只读；结果写入验收记录）
curl --noproxy '*' -s "http://localhost:18060/health"
# 用专用数据小号登录后执行一次搜索，检查返回 JSON 是否含 author.fans

# 1. 手动触发 M2（mock/录制响应也可）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_sample/run"
sqlite3 data/app.db "SELECT source,COUNT(*) FROM hot_items GROUP BY source;"

# 2. 查看低粉爆款样本
curl --noproxy '*' -s "http://127.0.0.1:8000/api/viral-samples?domain=AI与编程" | python -m json.tool
sqlite3 data/app.db "SELECT id,domain,viral_score,reason FROM viral_samples ORDER BY viral_score DESC LIMIT 10;"

# 3. 人工喂样本（降级模式）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/viral-samples/manual" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.xiaohongshu.com/explore/demo","title":"低粉爆款样本","author":"demo","fans":100,"likes":100,"collects":50,"comments":20,"domain":"AI与编程"}'

# 4. 检查自动建题与撞题证据
sqlite3 data/app.db "SELECT id,title,source,status,score,evidence FROM topics WHERE source='radar' ORDER BY id DESC LIMIT 5;"

# 5. 触发周度拆解（mock LLM 可验管线）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_teardown/run"
sqlite3 data/app.db "SELECT id,title_pattern,reason FROM viral_samples ORDER BY id DESC LIMIT 5;"
sqlite3 data/app.db "SELECT domain,tag,heat FROM tag_library ORDER BY heat DESC LIMIT 10;"

# 6. 熔断演练（临时指向无效 mcp 地址后连续触发 3 次）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_sample/run"
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_sample/run"
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/collectors/xhs_sample/run"
sqlite3 data/app.db "SELECT name,status,consecutive_failures FROM collector_state WHERE name='xhs_sample';"
# 熔断后再次触发应被拒绝；人工恢复后才可继续
```

## 【API 契约草案（实现时以 SDD 第 8 章为准补齐错误码）】

- `POST /api/collectors/xhs_sample/run`
  - 200：`{"inserted": n, "deduplicated": n, "filtered": n, "viral_created": n, "circuit_open": false}`
  - 409：采集器已熔断，响应说明需人工恢复。
  - 502：mcp 服务不可达或返回异常；失败计数 +1。
- `GET /api/viral-samples?domain=<可选>`
  - 200：按 `viral_score` 倒序返回样本，含 hot_item 关键互动数据与 evidence 摘要。
- `POST /api/viral-samples/manual`
  - 201：人工样本创建成功，返回 `hot_item_id / viral_sample_id / topic_id（若建题）`。
  - 400/422：URL、fans、likes、collects、comments、domain 缺失或非法。
- `POST /api/admin/collectors/xhs_sample/resume`
  - 200：人工恢复成功，失败计数清零。
  - 409：未处于熔断状态。

## 【P4 依赖边界】

P-1b 只负责产生可信的数据资产与初版规则，不在本阶段完成 P4：

- P4 会基于 `publish_records` 回填数据校准 `VIRAL_MAX_FANS / VIRAL_MIN_LIKES / VIRAL_MIN_SCORE / TOPIC_DUPLICATE_JACCARD`；本阶段只写可热改初值。
- P4 会按真实发布效果调整 `topics.score` 排序；本阶段只保证自动建题含完整 `evidence` 快照。
- P4 会分析成本、发布效果与选题命中率；本阶段不做报表、不做评分回填计算、不做阈值自动调整。
- P5 内容查重未来可复用 `viral_samples` 原文证据做 n-gram 相似度比对；本阶段不提前实现查重。
