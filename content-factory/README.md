# content-factory · 双端内容工厂

公众号 + 小红书双端矩阵内容系统。上游事实来源：《双端内容工厂 · 开发计划书 v1.3》。
本仓库当前进度：**P-1b 低粉爆款引擎**（M2 小红书采样器 + M3 选题雷达分析；P0-P3 文本/出图/素材包链路已就绪）。

> P-1a 已完成：8 表建表、采集/调度/备份/告警/雷达分析就位，`topics` 表有 radar 选题可供生成测试。

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
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 按需改 RSSHUB_BASE_URL / NOTIFY_WEBHOOK
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

手动触发一次采集（第 8 章调试接口）：

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
- `data/domains.yml`：领域关键词表，改词表不改代码；命中才入库并自动建选题。
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
其余模块（M6 草稿箱等）按路线图在后续阶段交付。

## 定时任务（APScheduler）

| 任务 | 频率 | 说明 |
| --- | --- | --- |
| hotboard | 每小时 | 热榜采集（启动即跑一轮） |
| expire_topics | 每小时 | 到期 radar 选题归档（created_at + 72h） |
| xhs_sample | 每 6 小时 | 小红书只读采样（M2，P-1b；熔断后跳过，等人工恢复） |
| xhs_teardown | 每周一 06:00 | 低粉爆款周度 LLM 拆解（A3，P-1b） |
| backup | 每日 03:00 | SQLite 备份到 `data/backups/app_YYYYMMDD.db`，保留 7 份 |
| cleanup | 每周日 05:00 | 物理删除 90 天前的 hot_items（与备份错开 ≥ 1 小时） |
