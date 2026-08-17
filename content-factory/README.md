# content-factory · 双端内容工厂

公众号 + 小红书双端矩阵内容系统。上游事实来源：《双端内容工厂 · 开发计划书 v1.3》。
本仓库当前进度：**P0 平台抽象重构**（M4 Prompt 策略引擎 + M5 内容生成服务骨架，platform=wechat）。

> P-1a 已完成：8 表建表、采集/调度/备份/告警/雷达分析就位，`topics` 表有 radar 选题可供生成测试。

## Non-goals（明确不做，计划书第 2 章）

| 不做的功能 | 原因 |
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

### 端到端 curl 验收（需起服务）

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 1. 生成（未配 OPENAI_API_KEY 时自动走 mock 降级）
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=wechat"
sqlite3 data/app.db "SELECT id,status,platform,meta FROM articles ORDER BY id DESC LIMIT 1;"
# 2. 模板热更新（不重启即生效）
sqlite3 data/app.db "UPDATE prompts SET template=REPLACE(template,'信息密度高','改过的文案') WHERE id=1;"
curl -X POST "http://127.0.0.1:8000/api/topics/2/generate?platform=wechat"
# 3. 重新生成开新行 + 旧行归档
sqlite3 data/app.db "SELECT id,status FROM articles WHERE topic_id=1 ORDER BY id DESC;"
```

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
- `data/sensitive_wechat.txt`：公众号敏感词表，改词表不改代码；命中即标 `status=failed`
  并在 `error` 注明命中词。空表占位，冷启动由人工整理后滚动扩充。

## 目录

按计划书第 3 章目录约定；P-1a 交付 `models.py`（第 5 章 8 张表全量）、
`collectors/{base,hotboard}.py`、`services/{radar,notify,scheduler}.py`、
`api/routes_admin.py`。P0 交付 `services/{prompt_engine,generator,sensitive}.py`、
`api/routes_topics.py`、`schemas.py`（补 `WechatArticle`）、`prompts/wechat_article.yml`。
其余模块（M2 采样、M6/M7 适配器、M8 素材包、小红书词表）按路线图在后续阶段交付。

## 定时任务（APScheduler）

| 任务 | 频率 | 说明 |
| --- | --- | --- |
| hotboard | 每小时 | 热榜采集（启动即跑一轮） |
| expire_topics | 每小时 | 到期 radar 选题归档（created_at + 72h） |
| backup | 每日 03:00 | SQLite 备份到 `data/backups/app_YYYYMMDD.db`，保留 7 份 |
| cleanup | 每周日 05:00 | 物理删除 90 天前的 hot_items（与备份错开 ≥ 1 小时） |
