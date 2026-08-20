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

- **M2 采样器 `app/collectors/xhs_sample.py`**：RedFox 爆款洞察单源只读采样
  （按调用计费，需 `REDFOX_API_KEY`）；禁止任何写/互动接口。曾并存的
  xiaohongshu-mcp 本地降级源已于 2026-08-20 废弃（本地服务未部署，降级只会把
  RedFox 的真实故障盖成「连接被拒绝」）。URL 去重、领域过滤沿用采集统一协议。
- **M3 打分 `app/services/radar.py`**：`viral_score = (likes + 2×collects + 3×comments) ÷ max(fans,1)`，
  实时判定纯规则不调 LLM；阈值 `CF_VIRAL_FANS_MAX=5000 / CF_VIRAL_LIKES_MIN=500 / CF_VIRAL_SCORE_MIN=2.0`
  环境变量可热改（P4 校准）。入选样本写 `viral_samples` 并自动建 `topics(source=radar, status=new)`
  （发现不决策），撞题去重走标题 Jaccard ≥ 0.5（`CF_TOPIC_DUPLICATE_JACCARD`）。
- **fans 探针结论（docs/p-1b-fans-probe.md）**：RedFox 搜索结果自带 authorFans
  （`fans_available=true`），低粉爆款判定直接跑通；fans 缺失的条目只落
  `hot_items` 笔记级数据，可经 `POST /api/viral-samples/manual`
  （或管理页 `/viral` 人工喂样本表单）补齐 fans 后进入同一管线。
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

### P-1b 真实质量验收（需 RedFox Key）

1. `.env` 配置 `REDFOX_API_KEY`；`.venv/Scripts/python -m app.collectors.xhs_sample probe "AI工具"`
   重跑 fans 探针，结论更新进 `docs/p-1b-fans-probe.md`。
2. 连续 3 天采样（`POST /api/collectors/xhs_sample/run`、`/viral` 手动采样或栏目定向采样）；
   验收线：`viral_samples ≥ 5`，人工抽查过半数"确实值得仿写"。
3. 熔断演练的结构验收（熔断 + 告警一次 + 409 + 人工恢复）由 `tests/test_p1b.py` 覆盖；
   真实环境 RedFox 连续故障熔断后，恢复需 `POST /api/admin/collectors/xhs_sample/resume` 显式解除。
4. 采样期间严禁任何关注、点赞、评论、私信、回关、发布操作。

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
- RedFox 按调用计费：一次手动/定时采样 ≈ 关键词数 次调用（每词最多 1 次）；
  任务去重与熔断是主要的防误触机制，`CF_XHS_SAMPLE_SCHEDULED=false`
  可完全关闭定时采样只留手动。

### 测试与 CI

```bash
.venv/Scripts/python -m pytest tests -q      # 正式用例：迁移/领域/采样任务/lifespan/安全渲染
.venv/Scripts/python tests/run_all.py        # 全量离线回归（pytest + 12 个验收脚本），任一失败非零退出
.venv/Scripts/python tests/_run_real_acceptance.py <输出目录>   # 真实 LLM 质量验收（计费，手动）
```

- GitHub Actions（`.github/workflows/tests.yml`）：装依赖 → `alembic upgrade head`
  空库升级 → pytest → `run_all.py`。全程离线（LLM mock / RedFox 桩 / 采样桩），
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

## 模型配置页（运行时切换文案/图片模型）

`/models` 维护多套大模型配置（名称、base_url、api_key、模型名、单价、思维链开关），**文案与图片各自设一个「当前使用」**，页面点一下即切换，下一次生成/出图即刻生效，无需重启或改 `.env`。

- 解析链：`model_config.resolve(purpose)` 每次调用前查库——该用途的 active 行优先，无则回退 `.env` 的 `OPENAI_*`（图片用途模型名回退 `GLM_IMAGE_MODEL`）。`.env` 从此只是回退默认，现有部署不配页面照常跑。
- mock 判定：显式 `CF_LLM_MOCK=1` 强制 mock；否则当前生效配置有 api_key 就真实调用（页面配了 key 而 .env 没配的场景由此支持），无 key 自动 mock 降级。
- 单价按模型配置折算：`meta.usage.model` 记实际调用的模型名、`cost_est` 用该配置的单价（空则回退 env 默认），`/stats` 成本报表按当月出现过的各模型分别列单价——多模型并存成本归因不失真。历史 usage 数据不追溯重算。
- 图片生成受 `.env` 的 `CF_IMAGEGEN_ENABLED` 总闸控制（防误计费硬开关）：总闸关时页面配了图片模型也不出图；封面提示词归纳仍走文案模型。
- api_key 安全：明文只存本地 `data/app.db`（`data/` 已 gitignore）；API/页面/日志一律掩码（`sk_ab****1234`），编辑表单不回填明文，留空即保持原 key。「测试」按钮对文案模型发 `max_tokens=8` 的极小请求，对图片模型发最小出图请求（**会真实计费 1 张**，页面上有确认提示）。
- 接口：`GET/POST /api/models`、`PUT/DELETE /api/models/{id}`、`POST /api/models/{id}/activate`、`POST /api/models/{id}/test`；迁移 `0003_model_configs` 启动自动执行。

## 小红书链路增强（素材摘录 + 平台上限校验 + 违禁词体检）

- **素材正文进提示词**：evidence 快照带素材摘录（RedFox 的 `raw.article.desc`，截 200 字），生成时 `reference_points` 从「标题（链接）」升级为「标题（链接）：正文摘录」——模型仿写/深挖有真实内容可依，落实 v4 模板「素材里没有的不编造」的铁律；GitHub 仓库行的 desc 已含在标题里，不重复带。旧选题 evidence 无 desc 字段也兼容（缺省不带）。
- **平台硬上限校验**：`XhsNote` 标题 ≤ 20 字（小红书发布上限）、正文 ≤ 950 字（给末尾标签行留余量，合计不超 1000 字上限）。超限直接生成失败进重试，报错文案拼进下一轮提示让模型自纠，免得发布前手动剪文案。封面/金句超长仍走 imaging 自动缩字号的软约束。
- **违禁词体检（发布前质检）**：xhs / wechat ready 文章页「违禁词体检」按钮 → RedFox 违禁词库检测标题+正文，**平台随文章**（xhs 走小红书词库、wechat 走微信公众号词库，命中词分别回填 `sensitive_xhs.txt` / `sensitive_wechat.txt`；**按调用计费，页面 confirm 后才发请求**，不进自动链路）→ 命中词逐个可点「＋词」回填本地词表（文件追加、去重、即时生效），下次生成直接在本地方向拦截——词表滚动扩充的既定路径。英文子串误报按 skill 同款规则剔除（如 "av" ⊂ "Gravitas" 不算命中，避免回填词表后误杀含英文的文章）。接口：`POST /api/articles/{id}/sensitive-check`（非 xhs/wechat 平台 409，RedFox 错误 502）、`POST /api/sensitive/{platform}/words`（单批 ≤50 词、单词 ≤50 字，防误杀整站的超短词在服务层拦截）。
- 敏感词表本体仍是文件即数据（`data/sensitive_xhs.txt` / `data/sensitive_wechat.txt`，每行一词），冷启动占位词待人工按「所涉领域监管词 + 平台违禁词」滚动扩充，体检回填是加速器不是替代。

## P9 公众号数据链路（优质库采样 → 建题 → 生成 → 发布闭环）

- **RedFox 优质库采样**：`searchArticle`（sortType=`_4` 最热，每关键词 1 页 20 条 = 1 次计费，offset 翻页留待深采）。`GzhSampleCollector`（`gzh_sample`）与 xhs 同一套持久化采样队列（入队 → 逐关键词执行 → 进度落库），入口：工作台灵感选题区「公众号采样」/ 素材采样页；**不进定时任务**（计费纪律，定时仍仅 xhs）。关键词优先级同 xhs：显式传入 > `CF_GZH_SAMPLE_KEYWORDS` > 启用栏目词池 > 领域词表，上限 `CF_GZH_SAMPLE_MAX_QUERIES`（默认 20）。
- **爆款判定（阅读量口径）**：优质库无粉丝字段，不复制「低粉」概念——`gzh_viral_score = (likes + watches + 2×collects + 3×shares + 3×comments) ÷ max(reads,1)`，入选条件 `reads ≥ CF_GZH_READS_MIN（默认 10000）且 score ≥ CF_GZH_SCORE_MIN（默认 0.08）`（阅读量是互动密度的分母，下限属于指标有效性而非预筛）。**gzh 条目绝不走 xhs 判定**——fans 恒 0 会让爆文率除以 1、全数误判。reads/watches/shares 存 `raw.article`，evidence 快照一并带上。阈值与初值口径记录在 `docs/p4-calibration.md`，校准视图（`/stats`）按源分流复判。
- **手动喂样本（URL 抓取式）**：素材采样页贴公众号文章链接 + 领域 → `queryArticleDetail`（1 次计费，confirm 后才发）抓全量指标与正文 → 与自动采样同一打分/落库/撞题/建题管线。比手填互动数准确；URL 重复 409（**计费调用前先查重**，不白花），RedFox 失败 502 不写半成品。接口：`POST /api/viral-samples/gzh-manual`。
- **生成质量**：`WechatArticle` 平台硬上限校验（标题 30 字 / 摘要 54 字折叠位 / 正文 3000 字，超限生成失败进重试、报错拼进提示自纠）；`prompts/wechat_article.yml` 升 v2（接 P5b 系列上下文 + 标签候选 + 公众号写作规范：开头钩子 200-300 / 主体 800-1000 二级标题分段 / 结尾收束 200-300 + 互动引导），v1 行保留、选模板自动取 enabled 最大版本；标题打分平台化——wechat 四维（赛道匹配 15 / 点击诱因 35 / 结构合规 15 / 爆文潜质 35，0-100 制，S≥90/A≥70/B≥50）与 xhs 六维（0-10 制）同响应 JSON 形状，文章页按钮按平台出「四维打分 / 六维打分」。
- **PIL 封面 + 文稿素材包**：wechat 生成 ready 自动出 900×383 PIL 封面（摘要优先、标题兜底；零计费本地渲染，不涉 AI 生图总闸 `CF_IMAGEGEN_ENABLED`；失败 → article failed，同 SDD 5.7 事务语义）。素材包放开 wechat：`title.txt + digest.txt + content.txt + images/01_cover.png`（发布后台摘要位直接粘贴）。
- **发布回填（公众号口径）**：文章页表单按平台出字段——wechat 阅读/在看/点赞/分享/收藏/评论，xhs 点赞/收藏/评论。效果分扩展 `+ watches×CF_SCORE_W_WATCHES(1) + shares×CF_SCORE_W_SHARES(3)`（reads 是受众规模量纲，同 fans 不进效果分；xhs 旧记录无这两键不受影响）。
- 真实接口验收脚本：`tests/_run_real_gzh_acceptance.py`（本地手动运行，不进 CI，3 次计费：searchArticle / queryArticleDetail / 违禁词 platform=微信公众号）。

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
`checklist_card` 独立渲染、字体缺失 → failed 且提示放置方法、wechat 回归
（P9 起生成自动出 1 张 PIL 封面并登记 assets）。

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
  `wechat_cover`（公众号封面 900×383，P9 起接入 wechat 生成链路）。
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
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`（P0 起）：产品内 LLM 的**回退默认**
  （OpenAI 兼容协议 + 强制 JSON mode）。日常切换模型用 `/models` 模型配置页（见上文
  「模型配置页」）；未设「当前使用」的用途才落到这三个变量。**当前生效配置没有
  api_key 时自动走 mock 降级**（返回固定 JSON、`meta.usage.model="mock"`），用于无 Key
  跑通链路结构；配 Key 后自动走真实 HTTP 调用。超时（默认 120s）、`max_tokens` 上限、
  回退单价等集中在 `config.py`。
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
