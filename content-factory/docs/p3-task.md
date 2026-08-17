# P3 · 素材包与预览页 — 任务四件套

> 阶段：P3（开发计划书第 9 章路线图第 5 阶段）
> 预估：1 天 · 模块：M8 素材包与预览页（计划书 6.4 / 7 / 8 章；SDD 3.2 M8 / 4.2 素材包契约）
> 前置：P2 已完成并通过验收（xhs 生成自动出图、assets 登记、版式与字体就位）
> 编码模型建议：GLM-5.2（计划书 13.2 分档：管理页 UI 一把生成是它的强项）

## 【目标】

完成素材包与预览页（P3），范围仅限 **M8**，其他模块不实现。具体交付：

1. **选题台 `GET /`**（Jinja2 渲染，不引前端框架）：选题列表按 score 倒序，展示
   状态 / 来源 / 领域 / 评分 / 保鲜截止；每条选题挂"生成"按钮（选择平台 wechat / xhs）
   与双端最新生成结果入口；雷达选题到期提示。
2. **文章预览页 `GET /articles/{id}`**（计划书 6.4 原文）：按 platform 分双栏——
   - 左侧文案区：标题 / 正文 / 标签三区，每个字段带"复制"按钮（`navigator.clipboard`）；
     xhs 的 content 已是末尾拼好 `#标签` 的可直接发布文案；wechat 显示 Markdown 原文
     与 meta.digest（HTML 渲染属 M6，不在页面上重复实现）。
   - 右侧图片区：assets 逐张预览 + 整包下载；图片经静态路由
     `/static/assets/{article_id}/{filename}` 提供（直挂 `config.ASSETS_DIR`，与 assets 表
     path 约定一致）。
   - 生成失败（failed）的文章展示 error 全文与"重新生成"入口；归档（archived）文章
     可预览但标注已归档。
3. **素材包下载 `GET /api/articles/{id}/package`**（SDD 4.2 契约）：仅 xhs 且 status=ready；
   返回 `application/zip`，内含 `title.txt`、`content.txt`（正文末尾已拼 `#标签`）、
   `images/`（`01_cover.png`、`02_quote.png`… 按上传顺序编号，文件名与 assets 表一致）。
   404 article 不存在；409 platform ≠ xhs / 无 assets / 状态非 ready。
4. **模板管理页 `GET /prompts`**：模板列表（platform / scenario / version / enabled /
   updated_at）、启停切换、新建版本编辑（新建即 version+1，旧版本保留——接口契约见
   计划书第 8 章 `GET/POST/PUT /api/prompts`）。本页是 M4 模板热更新的人工入口。
5. **发布回填入口 `POST /api/articles/{id}/publish` 的页面表单**（P4 评分计算不做，
   本阶段只做接口 + 表单落行）：platform / account / url / metrics（阅读/点赞/收藏
   可后续补）；成功后 article → published（终态），publish_records 只增不改。

**P3 不做的事**：富文本编辑器（M8 边界：内容不满意走"重新生成"，不在页面上改）、
M6 微信草稿箱推送（push-draft 接口与 HTML 渲染）、P4 的评分计算与成本报表、
docker compose 打包、登录鉴权（本地 127.0.0.1 单用户，迁移 VPS 前才补——SDD 6.2）。

## 【上下文】

- 项目根目录：`C:\YU\project\文案工厂\content-factory`
- 请先阅读：
  - `app/main.py`（路由挂载方式；管理页路由挂载风格与之保持一致）
  - `app/api/routes_topics.py`（generate 契约、状态机、`_build_variables` 变量组装）
  - `app/models.py`（Topic / Article / Asset / PublishRecord 字段）
  - `app/adapters/xhs.py`（content 已拼标签、meta.cover_note/image_plan 约定）
  - `app/config.py`（`ASSETS_DIR` 等目录常量）
  - `tests/test_p2.py`（mock 端到端造数据方式、TestClient 用法）
  - 开发计划书 6.4 节素材包与分发 / 第 7 章 M8 规格 / 第 8 章 API 清单
  - SDD 3.2 M8 边界 / 4.2 素材包契约与错误码 / 5.2 状态流转

### 已拍板的契约决策

1. **管理页零构建**：Jinja2 模板 + 少量原生 JS（fetch + clipboard），不引任何前端框架、
   不加构建步骤；静态资源（CSS/JS）放 `app/templates/static/` 或直接内联，保持单文件可读。
2. **ZIP 按需打包不落盘**：`/package` 用 `zipfile` 在内存（`io.BytesIO`）组装后流式返回，
   不在 data/ 下产生临时 zip 文件（避免与备份/清理任务相互干扰）。
3. **图片静态路由只读**：`/static/assets/` 只挂 `ASSETS_DIR`，且用 `article_id` 与
   `filename` 两段拼接、拒绝 `..`，防目录穿越。
4. **publish 回填幂等约束**：publish_records 只增不改（SDD 5.2）；同一 article 重复回填
   允许（多账号分发场景），但每次回填都把 article.status → published；对 published
   文章再触发生成仍按既有契约返回 409（P1 已拍板）。
5. **页面与 API 同源**：管理页全部通过第 8 章 API 取数，不私自直连数据库写旁路逻辑；
   页面没有的接口不补、接口没有的数据不靠模板硬凑。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `topics / articles / assets / publish_records`，
**不改表结构**。

- `publish_records`（P-1a 已建，本阶段首次写入）：`article_id / platform / account /
  url / metrics / published_at`；只增不改，article_id 永远指向发布时那一行。
- 回填副作用（SDD 4.2）：`articles.status → published`（终态）；topics.score 与模板
  效果分计算属 P4，本阶段不做。
- assets 读取约定：`kind=cover` 为封面、`kind=quote` 为金句图，按 id 升序即上传顺序。

## 【验收标准】

计划书 P3 原文：**从打开预览页到在小红书 App 完成发布，全程 ≤ 2 分钟；ZIP 解压即得
全部素材。** 展开为可执行条目：

### 结构验收（mock 模式，无 Key 可跑通）

1. 选题台 `/` 返回 200，列出种子选题且按 score 倒序；radar 选题显示来源徽标。
2. 预览页 `/articles/{id}`（xhs ready 行）：三区文案齐全、与 articles 行字段一致；
   图片区张数与 assets 行数一致，每张图可经 `/static/assets/...` 200 访问。
3. `GET /api/articles/{id}/package`：xhs ready → 200 + ZIP；解压后含 `title.txt`、
   `content.txt`（末尾即 `#标签` 行）、`images/01_cover.png`… 数量与 assets 行一致；
   wechat 行 / failed 行 / 不存在 id 分别 409 / 409 / 404。
4. 模板管理页 `/prompts` 返回 200 且列出两份种子模板；POST 新建版本后列表出现
   version+1；PUT 启停切换后 enabled 翻转，且 generate 立即按新状态选模板（热更新）。
5. 发布回填：POST `/api/articles/{id}/publish` 成功 → 201 + publish_records 行，
   article → published；对该 article 再 generate → 409。
6. 目录穿越防护：`/static/assets/../app.db` 之类路径 404/400，不返回文件内容。
7. `tests/test_p3.py` 可重复运行且通过；P0 / P1 / P2 回归（`test_p0/p1/p2.py`）仍通过。

### 质量验收（人工，需真实 DeepSeek Key 生成一篇后操作）

打开预览页 → 复制标题/正文/标签 → 保存图片 → 在小红书 App 完成发布，掐表 ≤ 2 分钟。
mock 模式下跳过，通过后在 README 记录实测耗时。

## 【纪律】

- 不实现计划书第 2 章非目标（自动发布、AI 生图、多账号、SaaS、视频、移动端）。
- **管理页不引前端框架**（计划书 2.5 / 4 章）：Jinja2 + 原生 JS，依赖克制，
  requirements.txt 不加新包（zipfile/io/shutil 全是标准库）。
- **无鉴权但只监听 127.0.0.1**（SDD 6.2）；不要在代码里预留"将来上公网"的开关，
  迁移 VPS 的鉴权检查单在 SDD 6.2，届时单独做。
- 文案即数据：页面文案（按钮名、提示语）集中模板，不写进 JS 逻辑深处。
- 素材包文件名契约严格按 SDD 4.2：`title.txt / content.txt / images/NN_kind.png`，
  改命名即违约。
- 每个接口附最小验证进 `tests/test_p3.py`，本地跑过才算完成，不接受"应该可以"。

## 【无 Key 降级方案】

沿用既有 mock：TestClient + `CF_LLM_MOCK` 下先造 xhs/wechat 各一篇（P2 后 xhs 自动带图），
再验证页面与接口。页面验收用 `TestClient` 断言 HTML 关键片段（字段值、按钮、图片
`<img src>` 与 assets 对应），不做浏览器端渲染测试——视觉验收走质量验收人工掐表。

## 【交付文件清单】

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `app/api/routes_pages.py` | 新建 | 管理页：`GET /`、`GET /articles/{id}`、`GET /prompts`（Jinja2） |
| `app/api/routes_articles.py` | 新建 | `GET /api/articles`、`GET /api/articles/{id}`（含 assets 清单）、`GET /api/articles/{id}/package`（内存 ZIP）、`POST /api/articles/{id}/publish` |
| `app/api/routes_prompts.py` | 新建 | `GET/POST/PUT /api/prompts`（列表 / 新建版本 / 启停） |
| `app/templates/` | 新建 | `base.html`（布局与公共样式）、`topics.html`、`article.html`、`prompts.html` |
| `app/main.py` | 修改 | 挂三个新路由 + `/static/assets/` 静态路由（只读、防穿越） |
| `tests/test_p3.py` | 新建 | 结构验收 1–7（TestClient 页面断言 + ZIP 解压校验 + 回填 + 回归） |
| `README.md` | 修改 | 进度更新为 P3；管理页入口与素材包说明 |

## 【验收脚本示意】

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 1. 造一篇 xhs（mock 降级自动带图）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"

# 2. 素材包下载与解压校验
curl --noproxy '*' -OJ "http://127.0.0.1:8000/api/articles/1/package"
unzip -l *.zip     # 应含 title.txt / content.txt / images/01_cover.png…

# 3. 管理页
curl --noproxy '*' -s http://127.0.0.1:8000/ | head -30          # 选题台
curl --noproxy '*' -s http://127.0.0.1:8000/articles/1 | grep -o 'assets/1/[0-9_a-z.]*' | sort -u

# 4. 模板热更新链路（页面 → 库 → 下次生成）
curl --noproxy '*' -X PUT "http://127.0.0.1:8000/api/prompts/1" -H "Content-Type: application/json" \
  -d '{"enabled": false}'
curl --noproxy '*' -s "http://127.0.0.1:8000/api/prompts" | python -c "import sys,json; [print(p['id'],p['version'],p['enabled']) for p in json.load(sys.stdin)]"

# 5. 发布回填
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/articles/1/publish" \
  -H "Content-Type: application/json" \
  -d '{"platform":"xhs","account":"主号","url":"https://www.xiaohongshu.com/explore/xxx","metrics":{"likes":320,"collects":88,"comments":21}}'
sqlite3 data/app.db "SELECT status FROM articles WHERE id=1; SELECT platform,account FROM publish_records ORDER BY id DESC LIMIT 1;"

# 6. 回填后触发生成 → 409（published 终态）
curl --noproxy '*' -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs" -w "%{http_code}"
```

## 【页面信息架构（实现时可微调，信息密度不得减少）】

- `/` 选题台：表格列 = 选题标题 / 角度 / 领域 / 来源徽标（radar·manual）/ score /
  状态 / 保鲜倒计时 / 双端最新结果（平台徽标 + status 色点 + 详情链接）/ 操作
  （生成 wechat / 生成 xhs）。空态给"暂无选题，去触发采集"提示。
- `/articles/{id}`：头部 = 标题 + 平台徽标 + status + 创建时间 + usage 成本
  （`meta.usage.cost_est`）；左栏三区（标题/正文/标签）各自复制按钮；右栏图片网格 +
  "下载素材包"按钮（xhs 且 ready 时可用，否则禁用并说明原因）；底部回填表单
  （platform / account / url / likes / collects / comments）。
- `/prompts`：按 platform+scenario 分组，组内按 version 倒序；每行启停开关 +
  "基于此版本新建"展开编辑区（模板全文 textarea + 变量清单只读展示）。
