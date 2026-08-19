# content-factory · 双端内容工厂

公众号 + 小红书双端矩阵内容系统。上游事实来源：《双端内容工厂 · 开发计划书 v1.3》。
本仓库当前进度：**P-2 长期运行基础设施**（领域词表入库、持久化采样任务 + 可恢复 worker、
Alembic 迁移、pytest/CI）；此前 P0-P4 文本/出图/素材包/数据飞轮、P-1b 低粉爆款引擎均已就绪。

> P-1a 已完成：8 表建表、采集/调度/备份/告警/雷达分析就位，`topics` 表有 radar 选题可供生成测试。

## P4 数据飞轮

发布 → 回填 → 评分 → 反哺的闭环（回填永远由人工发起，无任何自动抓取）：

- **评分重算 `app/services/scoring.py`**：`POST /api/articles/{id}/publish` 回填成功后自动触发
  `recompute()`，也可手动全量重算。公式（拍板记录见 `docs/p4-calibration.md`）：
  `score = base_score + SCALE × log1p(Σ(likes×1 + collects×2 + comments×3))`，
  求和范围为该 topic 全部已发布文章的回填数据；幂等重算，补录后重跑即更新；
  无回填选题评分不变（不拉低未发布选题）。权重与缩放走环境变量
  `CF_SCORE_W_LIKES / CF_SCORE_W_COLLECTS / CF_SCORE_W_COMMENTS / CF_SCORE_EFFECT_SCALE`。
  `GET /api/topics` 与选题台 `/` 均按 score 倒序。
- **模板效果分**（派生报表，不落字段、不自动启停模板）：`GET /api/prompts/stats` 按
  platform+scenario+version 聚合已发布文章的互动均值（每篇多条回填先合并再平均）；
  生成落库时在 `articles.meta` 记录 `prompt_id / prompt_version`（不改表），
  无版本记录的历史文章归"未知版本"组；published < 10 篇（`CF_PROMPT_STATS_MIN_SAMPLES`）
  只展示并标"样本不足"。
- **成本报表**：`GET /api/stats/cost?month=YYYY-MM` 双端 tokens / 文章数 / cost_est 合计，
  直接给出 `xhs_avg_cost_per_article`（回答"一篇小红书笔记平均生成成本是多少"）；
  `GET /api/stats/cost/article/{id}` 单篇明细。口径为 **cost_est 估算值**——切 GLM 后必须用
  `CF_LLM_PRICE_INPUT / CF_LLM_PRICE_OUTPUT` 按官方价修正，否则不作账单依据。
- **阈值校准视图**：`GET /api/stats/threshold-calibration` 展示 viral_samples 判定 ×
  自有账号实际发布效果交叉表 + 当前阈值；校准结论由周四校准会人工拍板 → 改环境变量 →
  记录到 `docs/p4-calibration.md`（radar 现读 config，改值即生效；系统不自动改阈值）。
- **报表页 `/stats`**：成本（大字 xhs 平均单篇成本 + 历史月份小表）、模板效果、阈值校准三区；
  页面只读，不提供改阈值入口（防止绕过校准会纪律）。

### P4 验收脚本

```bash
.venv/Scripts/python tests/test_p4.py
```

覆盖结构验收 1-7：回填触发评分（publish_records 只增不改、排序反映回填效果、无回填选题
分数不变）、重算幂等与补录更新、模板效果分聚合与"未知版本"组、成本报表与 xhs 平均单篇
成本手工抽算一致、阈值校准交叉表与热改阈值即刻生效、报表页三区、meta 记录 prompt 版本。

### P4 真实质量验收缺口（顺延）

- P-1b 真实采样未完成（fans 探针降级中，见 `docs/p-1b-fans-probe.md`），阈值校准结论暂为
  "样本不足，维持初值"（已记录 `docs/p4-calibration.md`）。
- 真实发布回填 0 篇：待首篇小红书笔记人工发布并回填后，验证选题台排序变化与成本报表
  与 `meta.usage` 手工抽算一致；周四校准会用真实对照数据完成一次三阈值评审。

## P-1b 低粉爆款引擎

范围仅 M2（小红书采样）+ M3（低粉爆款打分与自动建题）：

- **M2 采样器 `app/collectors/xhs_sample.py`**：经 xiaohongshu-mcp（Docker 本机，
  `XHS_MCP_BASE_URL`，默认 `http://localhost:18060`）只读调用 `search_notes`；
  禁止任何写/互动接口。URL 去重、领域过滤沿用采集统一协议。
- **M3 打分 `app/services/radar.py`**：`viral_score = (likes + 2×collects + 3×comments) ÷ max(fans,1)`，
  实时判定纯规则不调 LLM；阈值 `CF_VIRAL_FANS_MAX=5000 / CF_VIRAL_LIKES_MIN=500 / CF_VIRAL_SCORE_MIN=2.0`
  环境变量可热改（P4 校准）。入选样本写 `viral_samples` 并自动建 `topics(source=radar, status=new)`
  （发现不决策），撞题去重走标题 Jaccard ≥ 0.5（`CF_TOPIC_DUPLICATE_JACCARD`）。
- **fans 探针结论（docs/p-1b-fans-probe.md）**：本开发环境未部署 mcp，`fans_available=false`，
  走降级模式——自动采样只落 `hot_items` 笔记级数据，低粉爆款经
  `POST /api/viral-samples/manual`（或管理页 `/viral` 人工喂样本表单）补齐 fans 后进入同一管线。
  真实环境部署 mcp 后执行 `python -m app.collectors.xhs_sample probe` 重跑探针并更新该文档即可，
  代码无感切换（条目 fans>0 即自动参与判定）。
- **周度拆解（附录 A3）**：每周一 06:00 调度（`CF_XHS_TEARDOWN_WEEKDAY/HOUR`），把当周
  `viral_samples` 交 LLM 总结标题模式/情绪词/结构套路，结论回写样本 `reason` 并累计
  `tag_library.heat`；手动触发仅调试用：`POST /api/collectors/xhs_teardown/run`。
  模板种子 `prompts/xhs_teardown.yml`（幂等键 `xhs+teardown+v1`），无 Key 走 mock。
- **熔断**：M2 连续失败 3 次（`CF_COLLECTOR_CIRCUIT_FAILURES`）自动熔断并经 `NOTIFY_WEBHOOK`
  告警一次；熔断期间触发返回 409，只能 `POST /api/admin/collectors/xhs_sample/resume` 人工恢复，
  不自动重试不自愈。状态见 `GET /api/admin/collectors` 或 `/viral` 页面。

### P-1b 验收脚本

```bash
.venv/Scripts/python tests/test_p1b.py
```

覆盖结构验收 1-9：探针记录、M2 协议（mock 响应/去重/过滤/fans 降级）、打分边界
（0.1 / 2.6 / fans=0 不除零）、落库建题证据、撞题合并（score 取大、evidence 追加）、
人工喂样本 422/409、A3 幂等入库与 mock 拆解回写、熔断演练（告警一次、409、人工恢复）。

### P-1b 真实质量验收（需专用数据小号与 mcp 服务）

1. 按仓库外操作说明用 Docker 起 xiaohongshu-mcp，专用数据小号扫码登录（凭据不进仓库）；
   `.env` 配 `XHS_MCP_BASE_URL`。
2. `.venv/Scripts/python -m app.collectors.xhs_sample probe "AI工具"` 重跑 fans 探针，
   结论更新进 `docs/p-1b-fans-probe.md`。
3. 自动模式：连续 3 天采样（`POST /api/collectors/xhs_sample/run` 或等调度）；
   降级模式：连续 3 天经 `/viral` 页面人工录入。验收线：`viral_samples ≥ 5`，
   人工抽查过半数"确实值得仿写"。
4. 熔断演练：`XHS_MCP_BASE_URL` 临时指向无效地址连触 3 次，确认熔断 + 告警一次 + 409；
   恢复服务后仍需 `POST /api/admin/collectors/xhs_sample/resume` 显式解除。
5. 采样期间严禁任何关注、点赞、评论、私信、回关、发布操作。

## P-2 长期运行基础设施（领域词表入库 + 持久化采样任务 + 迁移/测试）

把"单进程本地工具"升级为可长期运行、可恢复、便于多人继续扩展的结构。

### 数据库迁移（Alembic）

- schema 变更一律走 `migrations/`（版本文件是冻结 DDL，不随模型漂移）；启动时
  `init_db()` 自动做迁移引导：空库全量升级；create_all 时代的旧库先自动备份
  （`data/backups/pre_migrate_*.db`，不参与每日备份轮换、永久保留）再 stamp 基线
  后增量升级；已是 head 则幂等直返。
- 手动命令（Git Bash，在 `content-factory/` 下）：

```bash
.venv/Scripts/python -m alembic -x db=data/app.db upgrade head   # 升级到最新
.venv/Scripts/python -m alembic -x db=data/app.db current        # 查看当前版本
.venv/Scripts/python -m alembic revision -m "变更说明"            # 新增空版本脚本
```

### 领域词表（数据库为唯一事实源）

- `domains` / `domain_keywords` 两表（名称唯一、领域内关键词唯一、ordering 编码
  匹配优先级：自定义领域 10+，官方 24 类目 1000+，多领域命中取先声明者）。
- `data/domains.yml` 只作首次导入的种子（启动幂等导入，之后不再读不写）；运行时
  改词表走 API：`GET/POST /api/domains`、`POST /api/domains/{name}/keywords`、
  `PUT /api/domains/{name}/enabled`。建栏目时"领域登记 + 关键词登记 + 栏目插入"
  在同一事务，历史 `topics.domain` / `pillars.domain` 仍是字符串快照不加外键。

### 持久化采样任务与 worker

- `sampling_jobs` 表：queued → running → succeeded / succeeded_empty（全关键词无结果，
  合法不熔断）/ failed / blocked（领取时熔断）/ canceled。逐关键词提交进度
  （计数器 / 当前词 / 心跳 / 租约），素材入库与进度同一事务，崩溃最多丢当前词。
- **API 进程不再执行付费网络请求**：`POST /api/sampling/jobs` 入队即返回 202 +
  job_id；`POST /api/collectors/xhs_sample/run` 兼容改为入队；APScheduler 的
  xhs 定时任务只入队。任务查询 / 取消（仅 queued）/ 重试（终态保留进度续跑）
  见 `/api/sampling/jobs` 系列端点，`/viral` 页有任务列表与进度。
- 去重与重试规则：同一来源的活跃任务只允许一个（`pillar:{id}` / `manual:xhs_sample` /
  `scheduled:xhs_sample` 去重键，连点不重复计费）；任务失败计入熔断计数（与同步
  路径同语义）；租约超时的 running 任务自动回队续跑，尝试次数用尽判失败。
- 运行方式：

```bash
# 单机开发（默认）：API 进程内嵌 worker 线程，一条命令全跑
.venv/Scripts/uvicorn app.main:app --host 127.0.0.1 --port 8000

# 长期/多人部署：API 与 worker 分进程（先在 .env 设 CF_WORKER_EMBEDDED=0）
.venv/Scripts/uvicorn app.main:app --host 127.0.0.1 --port 8000   # 进程 1：API
.venv/Scripts/python -m app.services.worker                       # 进程 2（可多个）：worker
.venv/Scripts/python -m app.services.worker --once                # 补跑一个任务即退出（调试）
```

worker 领取是条件 UPDATE，多 worker 进程不会重复执行同一任务；相关开关：
`CF_WORKER_EMBEDDED` / `CF_WORKER_POLL_SECONDS` / `CF_WORKER_JOB_LEASE_SECONDS` /
`CF_WORKER_JOB_MAX_ATTEMPTS`。

### SQLite 边界与部署注意

- SQLite（WAL + busy timeout 30s）适合本机/小团队低并发写；API + worker 分进程、
  每日备份的现状在几十次写/分钟内没有压力。出现 `database is locked` 常态化、
  多机访问或要并发 worker 数 > 4 时，迁 PostgreSQL（SQLAlchemy 换连接串 + 迁移链
  重写为 PostgreSQL 方言，业务代码不动）。
- 当前只绑 `127.0.0.1`，Host/Origin 白名单只防 DNS rebinding 与跨站写，**不是
  用户认证**：暴露到局域网/公网前必须先加认证（反向代理 Basic Auth 或接入登录），
  否则任何人可触发付费采样与删除数据。
- RedFox 按调用计费：一次手动/定时采样 ≈ 关键词数 次调用（每词最多 1 次 RedFox、
  失败降级 mcp 不重复计费）；任务去重与熔断是主要的防误触机制，`CF_XHS_SAMPLE_SCHEDULED=false`
  可完全关闭定时采样只留手动。

### 测试与 CI

```bash
.venv/Scripts/python -m pytest tests -q      # 正式用例：迁移/领域/采样任务/lifespan/安全渲染
.venv/Scripts/python tests/run_all.py        # 全量离线回归（pytest + 12 个验收脚本），任一失败非零退出
.venv/Scripts/python tests/_run_real_acceptance.py <输出目录>   # 真实 LLM 质量验收（计费，手动）
```

- GitHub Actions（`.github/workflows/tests.yml`）：装依赖 → `alembic upgrade head`
  空库升级 → pytest → `run_all.py`。全程离线（LLM mock / RedFox 桩 / 本地 mock mcp），
  不消耗任何付费 API；真实 RedFox/LLM 验收保持显式手动。
- `run_all.py` 覆盖的验收脚本各自用临时库隔离，绝不写 `data/app.db`；
  `_run_real_acceptance.py` 的数据库/素材/备份全部重定向到输出目录。

## P3 管理页与素材包

启动服务后访问 `http://127.0.0.1:8000/` 进入选题台，`/articles/{id}` 查看文章预览与图片，`/prompts` 管理模板版本。小红书 ready 文章可从预览页下载素材包；ZIP 内含 `title.txt`、`content.txt` 与按上传顺序编号的 `images/NN_kind.png`。发布链接和阅读/点赞/收藏等指标可在预览页回填，记录追加保存且文章进入 `published` 状态。

### P3 验收脚本

```bash
.venv/Scripts/python tests/test_p3.py
```

`test_p3.py` 覆盖选题台排序和雷达来源、文章预览与受控素材访问、内存 ZIP 素材包、模板新版本与启停热更新、发布回填和目录穿越防护。

## Non-goals（明确不做，计划书第 2 章）
| --- | --- |
| 小红书自动化发布（Playwright、Cookie 注入、MCP 发布等一切形态） | 平台无官方发布 API，脚本发布触发风控，矩阵号限流封号风险不可接受 |
| AI 生图（扩散模型生成封面/配图） | MVP 用 PIL 模板图，零成本零风险；AI 生图留待后期增强 |
| 多账号矩阵调度、IP 池、设备指纹隔离 | 规模化运营阶段问题，MVP 先单账号跑通双端链路 |
| 自动互动（批量点赞、评论、私信、回关） | 高风险灰产行为，与合规原则直接冲突 |
| SaaS 化、多租户、计费 | 自用工具，无此需求 |
| 视频内容生成与发布 | 图文形态先行 |
| 移动端 App 或小程序 | 本地 Web 管理页已满足单用户场景 |

编码会话中如被提议实现上述功能，视为超出范围，直接拒绝。

## 快速开始

```bash
python -m venv .venv
# Windows（Git Bash）：
.venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env              # 按需改 RSSHUB_BASE_URL / NOTIFY_WEBHOOK / LLM / RedFox Key
.venv/Scripts/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Linux / macOS：
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动即自动完成：迁移引导（空库建表 / 旧库备份+升级，见「P-2 数据库迁移」）、
领域词表与提示词种子幂等导入、定时任务（`RUN_SCHEDULER=0` 可关）、内嵌采样 worker。

手动触发一次采集（第 8 章调试接口；xhs_sample 返回 202 + 任务 id，进度见 `/viral`）：

```bash
curl -X POST http://127.0.0.1:8000/api/collectors/hotboard/run
sqlite3 data/app.db "SELECT source, COUNT(*) FROM hot_items GROUP BY source;"
sqlite3 data/app.db "SELECT id, title, domain FROM topics WHERE source='radar' LIMIT 5;"
```

## P-1a 验收脚本（可重复运行）

```bash
.venv/bin/python tests/test_p1a.py        # 离线全量验证（fixture 数据源，不依赖外网）
bash tests/verify_p1a_live.sh             # 起服务后按附录 B 方式 curl + sqlite3 验收
```

`test_p1a.py` 覆盖：8 张表建表、采集入库、URL 去重（二次运行零新增）、领域过滤、
source=radar 选题生成与撞题合并、备份演练（保留 7 份）、告警演练（本地接收端）、
选题过期归档与 90 天清理。

## P0 验收脚本（可重复运行，无 Key 走 mock，不依赖外网）

```bash
.venv/bin/python tests/test_p0.py        # 离线全量验证（mock 模式 + 真实路径 patch，不依赖外网）
```

`test_p0.py` 覆盖：种子模板幂等入库（重启不覆盖）、端到端 mock 生成（articles 写 ready 行 +
`meta.usage` 记账 + topic→used）、模板热更新（改库不重启即对下次生成生效）、重新生成开新行+
旧行归档、敏感词命中→failed、404/409/400、真实 LLM 路径（成功/重试成功/3 轮失败）。

## P1 验收脚本（可重复运行，无 Key 走 mock，不依赖外网）

```bash
.venv/Scripts/python tests/test_p1.py        # 离线全量验证（mock 端到端 + 量规 + 适配器单元）
```

`test_p1.py` 覆盖：xhs+note+v1 种子模板幂等入库、端到端 mock 生成 platform=xhs
（ready 行 + `tags` JSON 数组 + `meta.usage/cover_note/image_plan`）、M7 文案适配
（正文末尾拼 `#标签`、与 tags 一一对应）、mock 产物满足 P1 量规（结构可测部分）、
重新生成开新行+旧行归档（xhs）、published 终态 409、xhs 敏感词命中→failed、
wechat 回归、适配器单元（标签去重去 #）。

真实 Key 质量验收（连续 3 篇按量规打勾）通过后，固定 5 个代表性 topic 快照到
`tests/golden/` 作为回归基线；mock 模式下不产出 golden set。

## P2 验收脚本（可重复运行，无 Key 走 mock，不依赖外网与系统字体）

```bash
.venv/Scripts/python tests/test_p2.py        # 离线全量验证（mock 端到端出图 + 版式单测）
```

`test_p2.py` 覆盖：端到端 mock 生成 platform=xhs 自动出图（1 封面 + ≥2 金句图，
`01_cover.png`… 编号、assets 登记 kind/尺寸/路径）、中文无乱码无截断（emoji 剥离、
超长自动缩字号、折行宽度）、重复生成不残留（开新行新目录干净、旧目录保留、
`render_assets` 替换旧行不重复登记）、`wechat_cover` 900×383 独立渲染、
`checklist_card` 独立渲染、字体缺失 → failed 且提示放置方法、wechat 回归（不出图）。

### 端到端 curl 验收（需起服务）

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 1. 小红书生成（mock 降级）→ 自动出图
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
AID=$(sqlite3 data/app.db "SELECT id FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;")
ls "data/assets/$AID/"     # 01_cover.png、02_quote.png…
sqlite3 data/app.db "SELECT kind,path,width,height FROM assets WHERE article_id=$AID;"
# 2. 重新生成开新行 → 新目录干净、旧目录保留
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
```

## 配置要点（P2 新增）

- `data/fonts/`：思源黑体 SC Regular/Bold（OFL 授权，随仓库分发，来源与校验值见
  `data/fonts/README.md`）。imaging 只从这里加载字体，禁止依赖系统字体路径；
  缺失时生成 xhs 落 `failed` 并在 error 提示放置方法，绝不静默出乱码图。
- `data/imaging_templates/`：版式即数据（画布/背景/字体/字号/槽位/色值全在 YAML），
  改版式不重启即生效。四套：`emotion_cover`（小红书封面 1080×1440）、`quote_card`
  （金句卡片，生成链路用）、`checklist_card`（清单卡片，独立渲染验证，暂不接链路）、
  `wechat_cover`（公众号封面 900×383，调用方 M6 后续接入）。
- `CF_ASSETS_DIR` / `CF_FONTS_DIR` / `CF_IMAGING_TEMPLATES_DIR` / `CF_IMAGING_MIN_FONT_SIZE`：
  输出目录与字号下限（默认 28），超长文案自动缩小字号而非截断。
- 出图契约（SDD 5.7）：articles 与 assets 同一事务，出图失败（含字体缺失）article
  整体 `failed`，不留"有文案无图"的半成品。

## 配置要点（P0 新增）

## 配置要点

- `RSSHUB_BASE_URL`：生产用自建 RSSHub；离线调试可用 `file:///…/tests/fixtures`
  指向本地 RSS 文件（同一套解析与入库代码）。
- `NOTIFY_WEBHOOK`：统一告警出口（Server酱兼容 JSON POST）。告警事件（第 7 章）：
  采集器熔断/连续失败、备份失败、清理任务异常等。演练：
  `.venv/bin/python -m app.services.notify WARN test 通道演练 "P-1a 验收"`。
- `data/domains.yml`：领域关键词表的种子导入源（P-2 起词表存数据库，运行时改词表
  走 `/api/domains`，见「P-2 领域词表」）；命中才入库并自动建选题。
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`（P0 起）：产品内 LLM，OpenAI 兼容协议 +
  强制 JSON mode，只依赖这三个环境变量，供应商切换不改代码。**未配置 `OPENAI_API_KEY` 时
  自动走 mock 降级**（返回固定 JSON、`meta.usage.model="mock"`），用于无 Key 跑通链路结构；
  配 Key 后自动走真实 HTTP 调用。超时（默认 60s）、`max_tokens` 上限、单价等集中在 `config.py`。
- `data/sensitive_wechat.txt` / `data/sensitive_xhs.txt`：双端敏感词表（P1 起双端齐备），
  改词表不改代码；命中即标 `status=failed` 并在 `error` 注明命中词。空表/少量占位，
  冷启动由人工整理后滚动扩充。

## 目录

按计划书第 3 章目录约定；P-1a 交付 `models.py`（第 5 章 8 张表全量）、
`collectors/{base,hotboard}.py`、`services/{radar,notify,scheduler}.py`、
`api/routes_admin.py`。P0 交付 `services/{prompt_engine,generator,sensitive}.py`、
`api/routes_topics.py`、`schemas.py`（补 `WechatArticle`）、`prompts/wechat_article.yml`。
P1 交付 `schemas.py`（补 `XhsNote`）、`adapters/xhs.py`（M7 文案适配）、
`prompts/xhs_note.yml`（A1 种子）、`data/sensitive_xhs.txt`、`tests/test_p1.py`。
P2 交付 `services/imaging.py`（共享图文服务）、`adapters/xhs.py` 的 `render_assets`
（出图 + assets 登记）、`data/imaging_templates/` 四套版式、`data/fonts/` 思源黑体、
`tests/test_p2.py`。P-1b 交付 `collectors/xhs_sample.py`（M2 采样器 + fans 探针）、
`services/radar.py` 低粉爆款打分与周度拆解、`api/routes_viral_samples.py`（样本列表 +
人工喂样本）、`templates/viral.html`（`/viral` 管理页）、`prompts/xhs_teardown.yml`（A3 种子）、
`docs/p-1b-fans-probe.md`（探针记录）、`tests/test_p1b.py`。
P4 交付 `services/scoring.py`（评分重算 + 模板效果分 + 成本/校准报表）、
`api/routes_stats.py`（cost / prompts/stats / threshold-calibration）、
`api/routes_topics.py` 的 `GET /api/topics`、publish 回填后自动重算、
`templates/stats.html`（`/stats` 报表页）、`docs/p4-calibration.md`（公式拍板与校准记录）、
`tests/test_p4.py`。
其余模块（M6 草稿箱等）按路线图在后续阶段交付。

## 定时任务（APScheduler）

| 任务 | 频率 | 说明 |
| --- | --- | --- |
| hotboard | 每小时 | 热榜采集（启动即跑一轮） |
| expire_topics | 每小时 | 到期 radar 选题归档（created_at + 72h） |
| xhs_sample | 每 6 小时 | 小红书只读采样入队（M2，P-1b/P-2；P-2 起调度只入队，由 worker 执行；熔断后跳过入队，等人工恢复） |
| xhs_teardown | 每周一 06:00 | 低粉爆款周度 LLM 拆解（A3，P-1b） |
| backup | 每日 03:00 | SQLite 备份到 `data/backups/app_YYYYMMDD.db`，保留 7 份 |
| cleanup | 每周日 05:00 | 物理删除 90 天前的 hot_items（与备份错开 ≥ 1 小时） |
