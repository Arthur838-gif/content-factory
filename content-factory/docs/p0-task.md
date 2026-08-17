# P0 · 平台抽象重构 — 任务四件套

> 阶段：P0（开发计划书第 9 章路线图第 2 阶段）
> 预估：1 天 · 模块：M4 Prompt 策略引擎 + M5 内容生成服务骨架
> 前置：P-1a 已完成并通过验收（8 表已建、采集/调度/备份/告警/雷达分析就位、topics 表已有 5 条 radar 选题）

## 【目标】

完成平台抽象重构（P0），范围仅限 **M4 Prompt 策略引擎 + M5 内容生成服务骨架**，其他模块不实现。具体交付：

1. `articles` 已有 `platform` 字段（P-1a 建表时已带），无需 ALTER，无需回填。
2. 写死的 Prompt → 迁入 `prompts` 表：P0 只入库 **附录 A2 公众号文章模板**（platform=wechat, scenario=article）。A1 小红书模板留 P1，A3 拆解留 P-1b。
3. 生成链路改为五段式：**选题 → 选模板 → LLM → JSON → 渲染落库**（公众号存 Markdown，HTML 渲染属 M6，P0 不做）。
4. 跳过现状审计半步——当前项目无存量公众号系统代码，直接从零实现五段式。

## 【上下文】

- 项目根目录：`/Users/yu/Desktop/work/文案双工厂/6a7d8876dbb5a9b75f880477/content-factory`
- 请先阅读：
  - `app/models.py`（8 表全量合同）
  - `app/config.py`（环境变量约定）
  - `app/db.py`（会话）
  - `app/schemas.py`（P-1a 仅有 HotItem，需补 WechatArticle）
  - `app/main.py`
  - `app/api/routes_admin.py`（路由注册风格）
  - 开发计划书第 3 章目录约定 / 第 5 章数据模型 / 第 6.1 节生成链路时序 / 附录 A2 模板
  - SDD 第 4.2 节接口契约
- P-1a 已完成：8 表已建、采集/调度/备份/告警/雷达分析已就位，`topics` 表已有 5 条 radar 选题可供生成测试。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `topics / prompts / articles`。

- `articles.meta` 平台差异约定（公众号）：`{"digest": "...", "html": "...", "draft_media_id": "..."}`，其中 `html` 与 `draft_media_id` 留 M6 填，P0 只写 `digest`。
- 共有成本记账：`meta.usage = {"prompt_tokens": n, "completion_tokens": n, "model": "...", "cost_est": 美元估值}`，每次生成必写。
- 状态流转（合同）：生成成功 `status=ready`，失败 `status=failed` 且 `error` 必填；对同一 `(topic_id, platform)` 重新生成时**开新行**、旧的 ready/failed 行 → `archived`；已有 ready 未归档时返回 409。
- Prompt 幂等：种子以 `platform + scenario + version` 为键，已存在即跳过，重启绝不覆盖库内已改模板。

## 【验收标准】

计划书 P0 原文，无存量系统已做适配：

1. 新生成链路端到端跑通：对一个 radar 选题调 `POST /api/topics/{id}/generate?platform=wechat`，返回 `article_id` 且 `articles` 写入 `status=ready` 行、`meta.usage` 已记账。**无 DeepSeek Key 时走 mock 降级，链路结构与写库仍需正确**。
2. **模板热更新**：直接在数据库改 `prompts.template` 文案，不重启服务，下次生成即用新文案（prompt_engine 每次现读库，不进程缓存模板）。
3. `tests/test_p0.py` 可重复运行且通过（无 Key 时走 mock，不依赖外部服务）。

## 【纪律】

- 不实现计划书第 2 章非目标（小红书自动发布、AI 生图、多账号、SaaS、视频等）。
- P0 只支持 `platform=wechat`；小红书（xhs）留 P1。不实现 M6 微信草稿箱推送、不做 HTML 渲染、不做预览页。
- 先合同后实现：先确认 `schemas.py`（WechatArticle）与种子模板无误，再写业务逻辑。
- 文案即数据：Prompt、敏感词表都在库或文件，写进代码即返工。
- LLM 客户端只依赖 `OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME` 三个环境变量；强制 JSON mode、`max_tokens` 上限、重试封顶 2 次失败即落 `failed` 行。
- 依赖克制：`requirements.txt` 锁版本，只加 `openai` 或复用 `httpx` 直连（OpenAI 兼容协议用 httpx 即可，免引 SDK）。`jinja2` 需加入依赖。
- 所有超时值集中在 `config.py`（LLM 建议 60s）。

## 【无 Key 降级方案】（本阶段关键设计）

- `config.py` 检测：未配置 `OPENAI_API_KEY`（或 `CF_LLM_MOCK=1`）时，generator 自动走 mock 路径——返回一份符合 `WechatArticle` Schema 的固定 JSON，`meta.usage` 写占位值（`model: "mock"`），`status=ready`。
- mock 仅用于验收期跑通链路结构；真实 Key 配置后必须走真实 HTTP 调用（OpenAI 兼容 `/chat/completions` + `response_format={"type":"json_object"}`）。验收脚本要能在两种模式下都通过。
- 这是脚手架，不是产品行为：mock 不应让真实路径走样，真实调用代码与 mock 并列、由一个开关分流。

## 【交付文件清单】

- `app/schemas.py`：补 `WechatArticle`（title/digest/content_md）。
- `app/services/prompt_engine.py`（M4）：按 `platform+scenario` 取 `enabled` 中最高 `version`，Jinja2 渲染 system/user 消息对，不写死文案、不调 LLM、不缓存模板。
- `app/services/generator.py`（M5 骨架）：LLM 客户端（httpx 直连 OpenAI 兼容协议，JSON mode）、Pydantic 校验、失败重试 2 次（错误信息追加进提示）、`max_tokens` 上限、写 `meta.usage`、mock 降级分支。
- `app/services/sensitive.py`（共享骨架）：加载 `data/sensitive_wechat.txt`，提供命中检测；词表可先空文件，命中即标 `failed`。P1 补小红书词表时不动 M5。
- `prompts/wechat_article.yml`（A2 种子）+ 入库逻辑（幂等键 `wechat+article+v1`）。
- `app/api/routes_topics.py`：`POST /api/topics/{id}/generate?platform=wechat&prompt_id=<可选>`，404/409 处理按 SDD 4.2。
- `app/config.py`：新增 LLM 配置（base_url/key/model/max_tokens/timeout/mock 开关）。
- `data/sensitive_wechat.txt`：空文件占位。
- `tests/test_p0.py`：端到端（mock 模式）+ 模板热更新验证。

## 【验收脚本示意】

```bash
# 1. 端到端（mock 模式，无需 Key）
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=wechat"
sqlite3 data/app.db "SELECT id,status,platform,meta FROM articles ORDER BY id DESC LIMIT 1;"

# 2. 模板热更新（核心验收点）
sqlite3 data/app.db "UPDATE prompts SET template=REPLACE(template,'信息密度高','改过的文案') WHERE id=1;"
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=wechat"
# 不重启服务，确认新生成用了改后的模板

# 3. 重新生成开新行 + 旧行归档
sqlite3 data/app.db "SELECT id,status FROM articles WHERE topic_id=1 ORDER BY id DESC;"
```
