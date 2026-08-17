# P1 · 小红书文本生成 — 任务四件套

> 阶段：P1（开发计划书第 9 章路线图第 3 阶段）
> 预估：1 天 · 模块：M5 扩展 + M7 文案部分
> 前置：P0 已完成并通过验收（五段式链路跑通、模板热更新、M4 prompt_engine + M5 generator + sensitive 骨架就位）

## 【目标】

完成小红书文本生成（P1），范围仅限 **M5 扩展（XhsNote Schema + 小红书生成路径）+ M7 文案部分（正文末尾拼 #标签）**，其他模块不实现。具体交付：

1. `schemas.py` 补 `XhsNote`（title/content/tags/cover_text/image_quotes）。
2. 小红书 Prompt 模板入库：附录 A1（platform=xhs, scenario=note）。
3. `routes_topics.py` 的 `PLATFORM_SCHEMAS` 注册 `xhs`，生成接口支持 `platform=xhs`。
4. 小红书敏感词表 `data/sensitive_xhs.txt`（冷启动可少量或空，`sensitive.py` 已有双端支持）。
5. M7 文案部分：小红书适配器把 XhsNote 格式化为可直接发布的文案（正文末尾拼 `#标签`），写入 `articles.content`；`meta` 存 `cover_note` + `image_plan` + `usage`。
6. P1 完成后固定 golden set（5 个代表性 topic，快照当前产物）。

**P1 不做的事**：PIL 图文合成（P2）、素材包 ZIP（P3）、预览页（P3）、微信草稿箱推送（M6）。

## 【上下文】

- 项目根目录：`/Users/yu/Desktop/work/文案双工厂/6a7d8876dbb5a9b75f880477/content-factory`
- 请先阅读：
  - `app/models.py`（8 表合同）
  - `app/schemas.py`（已有 HotItem + WechatArticle，需补 XhsNote）
  - `app/config.py`（LLM/敏感词配置约定）
  - `app/services/generator.py`（M5 骨架，需扩展支持 XhsNote + mock）
  - `app/services/prompt_engine.py`（M4，种子目录需加 A1）
  - `app/services/sensitive.py`（已有双端支持，加 xhs 词表路径即可）
  - `app/api/routes_topics.py`（generate 接口，需注册 xhs 平台）
  - `prompts/wechat_article.yml`（种子格式参考）
  - 开发计划书第 5 章数据模型 / 第 6.1 节生成链路 / 附录 A1 小红书模板
  - SDD 第 4.2 节接口契约 / 第 5.6 节平台输出 Schema

### 已拍板的契约决策

P0 实现中发现的歧义已确认：**ready/failed 可重新生成（旧行归档、开新行），只有 published 终态行返回 409**。这是计划书 5.2 状态机的正确解释，SDD 4.2 的"409 已有 ready 行"为笔误。`routes_topics.py` 中的 `TODO(confirm)` 标注可去掉。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `topics / prompts / articles`。

- `articles.meta` 平台差异约定（小红书）：`{"cover_note": "封面文案", "image_plan": ["金句1", "金句2"], "usage": {...}}`。`cover_note` 来自 `XhsNote.cover_text`，`image_plan` 来自 `XhsNote.image_quotes`。
- 共有成本记账：`meta.usage` 与 P0 公众号一致，每次生成必写。
- 状态流转：与 P0 一致。生成成功 `status=ready`，失败 `status=failed`；重新生成开新行、旧行归档；published 终态返回 409。
- Prompt 幂等：A1 种子以 `platform+scenario+version = xhs+note+v1` 为键，已存在即跳过。

## 【验收标准】

### 结构验收（mock 模式，无 Key 也可跑通）

1. 对一个 radar 选题调 `POST /api/topics/{id}/generate?platform=xhs`，返回 `article_id` 且 `articles` 写入 `status=ready` 行、`platform=xhs`、`meta.usage` 已记账、`meta.cover_note` + `meta.image_plan` 已写。
2. `articles.content` 末尾已拼接 `#标签`（如 `#AI #副业 #赚钱`），可直接复制发布。
3. `tags` 字段存 JSON 数组（不含 `#` 号），与 `content` 末尾的 `#标签` 对应。
4. `tests/test_p1.py` 可重复运行且通过（mock 模式）。
5. P0 回归：`tests/test_p0.py` 仍全部通过。

### 质量验收（需真实 DeepSeek Key，mock 下跳过）

计划书 P1 原文：连续生成 3 篇，按下列量规逐条打勾，每篇 5 条过 4 条即合格（3 篇中 ≥ 2 篇合格）：

1. 标题 ≤ 20 字且含 emoji
2. 无说教腔（不出现"姐妹们记住""一定要知道"式口吻）
3. 正文每段 ≤ 3 行
4. 标签 3-5 个且与内容相关
5. 读完有收藏欲（信息可带走，非情绪水文）

**Key 到位后补跑质量验收**，通过后固定 golden set（5 个代表性 topic，快照产物存 `tests/golden/`）。

## 【纪律】

- 不实现计划书第 2 章非目标（小红书自动发布、AI 生图、多账号、SaaS、视频等）。
- P1 不做 PIL 图文合成（P2）、素材包 ZIP（P3）、预览页（P3）、微信草稿箱（M6）。
- 先合同后实现：先确认 `schemas.py`（XhsNote）与 A1 种子模板无误，再写业务逻辑。
- 文案即数据：Prompt、敏感词表都在库或文件，写进代码即返工。
- LLM 客户端复用 P0 已有的 `generator.py`，只扩展 Schema 和 mock 分支；不重复实现 HTTP 调用逻辑。
- M7 文案适配是纯文本处理（拼标签、格式化），不调 LLM、不出图、不调外部接口。
- 依赖克制：不引入新依赖（Pillow 留 P2，jinja2/httpx 已有）。
- 去掉 `routes_topics.py` 中的 `TODO(confirm)` 标注（已拍板：ready/failed 可重生成，published 返回 409）。

## 【无 Key 降级方案】

与 P0 一致：未配 `OPENAI_API_KEY` 或 `CF_LLM_MOCK=1` 时，generator 走 mock 分支。P1 需在 `generator.py` 的 `_MOCK_WECHAT` 旁新增 `_MOCK_XHS`，返回一份**刻意满足 P1 量规**的固定 XhsNote JSON（标题含 emoji、口语化短句、3-5 个标签、2-4 条金句），这样 mock 模式下结构验收能跑通，且验证 M7 文案适配（拼标签）正确性。

mock 固定 JSON 要精心构造，确保：
- 标题 ≤ 20 字且含 emoji ✓
- 正文每段 ≤ 3 行 ✓
- 标签 3-5 个 ✓
- 2-4 条 image_quotes ✓

## 【交付文件清单】

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `app/schemas.py` | 修改 | 补 `XhsNote`（title/content/tags/cover_text/image_quotes） |
| `app/services/generator.py` | 修改 | 新增 `_MOCK_XHS` + mock 分支支持 XhsNote |
| `app/services/prompt_engine.py` | 修改 | `SEED_FILES` 加 `"xhs_note.yml"` |
| `app/services/sensitive.py` | 修改 | 确认双端词表路径已支持 xhs（P0 骨架应已预留） |
| `app/api/routes_topics.py` | 修改 | `PLATFORM_SCHEMAS` 注册 `xhs` → XhsNote；去掉 TODO(confirm) |
| `app/adapters/xhs.py` | 新建 | M7 文案适配：XhsNote → 格式化正文（拼 #标签）+ meta(cover_note, image_plan) |
| `prompts/xhs_note.yml` | 新建 | A1 种子（幂等键 xhs+note+v1） |
| `data/sensitive_xhs.txt` | 新建 | 小红书敏感词表（冷启动可空或少量违禁词） |
| `tests/test_p1.py` | 新建 | 结构验收（mock 端到端 + 标签拼接 + meta 字段 + P0 回归） |
| `tests/golden/` | 新建目录 | golden set 占位（Key 到位后补快照） |

## 【验收脚本示意】

```bash
# 1. 小红书生成（mock 模式）
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
sqlite3 data/app.db "SELECT id,status,platform,meta FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;"

# 2. 确认标签拼接
sqlite3 data/app.db "SELECT content FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;" | tail -5
# 末尾应出现 #标签1 #标签2 ...

# 3. 确认 meta 字段
sqlite3 data/app.db "SELECT json_extract(meta,'$.cover_note'), json_extract(meta,'$.image_plan') FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;"

# 4. P0 回归：公众号生成仍正常
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=wechat"
sqlite3 data/app.db "SELECT status FROM articles WHERE platform='wechat' ORDER BY id DESC LIMIT 1;"

# 5. 重新生成开新行 + 旧行归档（xhs）
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
sqlite3 data/app.db "SELECT id,status FROM articles WHERE topic_id=1 AND platform='xhs' ORDER BY id DESC;"
```

## 【附录 A1 · 小红书笔记模板原文】（开发计划书 v1.3 附录 A1，P1 入库用）

```yaml
platform: xhs
name: 小红书爆款笔记
scenario: note
version: 1
variables:
  - title
  - angle
  - domain
  - tag_candidates
template: |
  # system
  你是一个小红书爆款博主，擅长写情绪化、有共鸣的短文。
  你的语言口语化、碎片化，像在和朋友聊天，绝不说教。

  # user
  选题：{{ title }}
  切入角度：{{ angle }}
  领域：{{ domain }}
  可选标签（参考，可增删）：{{ tag_candidates | join("、") }}

  请根据以上选题创作一篇小红书笔记，严格按以下 JSON 格式输出，不要输出任何其他内容：
  {
    "title": "带 1-2 个 emoji，含情绪词或数字，不超过 20 字",
    "content": "正文。口语化短句，每段不超过 3 行，段落之间空一行，总字数 300-800。不要出现'作为AI'、'综上所述'一类表达。",
    "tags": ["3-5 个相关标签，不要带 # 号"],
    "cover_text": "封面主标题文案，不超过 12 字，要有冲击力",
    "image_quotes": ["2-4 句金句，每句不超过 20 字，可直接印在图片上"]
  }
```
