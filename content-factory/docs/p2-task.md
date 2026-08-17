# P2 · 图文合成 — 任务四件套

> 阶段：P2（开发计划书第 9 章路线图第 4 阶段）
> 预估：1–2 天 · 模块：M7 图片部分 + 共享图文服务 imaging（M6 / M7 共用，SDD 3.1 归属共享层）
> 前置：P1 已完成并通过验收（XhsNote 链路、mock 分支、M7 文案适配、meta.cover_note / meta.image_plan 就位）

## 【目标】

完成图文合成（P2），范围仅限 **PIL 模板化出图 + assets 登记**，其他模块不实现。具体交付：

1. 共享图文服务 `app/services/imaging.py`：版式 YAML 加载、字体进程内缓存、中文自动换行、
   超长自动缩小字号（**不截断**）、渲染输出 PNG。本服务被 M6 / M7 共同调用，自身不调 LLM、
   不读写 topics / articles 业务表。
2. 四套版式配置 `data/imaging_templates/`（模板即配置，计划书 6.3）：
   - 小红书三套，画布统一 **1080×1440（3:4）**：情绪封面 `emotion_cover`、金句卡片 `quote_card`、
     清单卡片 `checklist_card`。其中 **checklist_card 本阶段只交付版式与渲染能力（单测验证），
     不接入生成链路**——XhsNote Schema 无清单字段，等后续 prompt 输出扩展再用。
   - 公众号封面一套 `wechat_cover`，画布 **900×383**（供 M6 后续调用，P2 只做独立渲染验证）。
3. xhs 生成链路接入出图：`generate platform=xhs` 成功后，用 `meta.cover_note` 出 1 张封面
   （`01_cover.png`）、`meta.image_plan` 逐句出金句图（`02_quote.png`、`03_quote.png`…），
   写入 `data/assets/{article_id}/` 并登记 `assets` 表。
4. 中文字体前置落地（计划书 00 章硬前提）：思源黑体（OFL）放入 `data/fonts/` 并随仓库分发；
   字体缺失时报错信息明确，**绝不静默出乱码图**。
5. 重复渲染清旧：对同一 article_id 渲染前先清空其 assets 目录并删除旧 assets 行（幂等，
   计划书 6.3"同一文章重复生成时先清旧文件"）。

**P2 不做的事**：素材包 ZIP 与预览页（P3）、M6 的 HTML 渲染与微信草稿箱推送、AI 生图
（计划书第 2 章非目标，PIL 模板合成不引扩散模型）、checklist_card 接入生成链路。

## 【上下文】

- 项目根目录：`C:\YU\project\文案工厂\content-factory`
- 请先阅读：
  - `app/models.py`（`Asset` 表合同，P-1a 已建，本阶段不改表）
  - `app/config.py`（`DATA_DIR` 目录约定；P2 新增 `FONTS_DIR / IMAGING_TEMPLATES_DIR / ASSETS_DIR`）
  - `app/adapters/xhs.py`（M7 文案适配；出图数据的来源是 `meta.cover_note` + `meta.image_plan`）
  - `app/api/routes_topics.py`（generate 第 3 步落库处是出图接入点）
  - `app/schemas.py`（`XhsNote.cover_text / image_quotes`）
  - `tests/test_p1.py`（mock 端到端的测试组织方式）
  - 开发计划书 6.3 节图文合成 / 第 7 章 M7 规格 / 第 00 章字体硬前提
  - SDD 3.1 节（imaging 为共享服务，M7 不自己画图）/ 5.1 节 assets 表 / 5.7 节一致性 /
    6.1 节（字体加载缓存进进程）

### 已拍板的契约决策

1. **出图失败 → article 整体 `failed`**（SDD 5.7：写 articles 与写 assets 同一事务提交，
   不允许出现"有文章无资产"的半成品）。error 注明 `图文合成失败：<原因>`；字体缺失属此类。
2. **字体与版式随仓库分发**：`.gitignore` 现有 `data/*` 白名单追加 `!data/fonts/` 与
   `!data/imaging_templates/`（思源黑体 OFL 授权可入库；`app.db`、`backups/`、`assets/`
   产物目录继续不入库）。
3. **assets 行是衍生资产、可重建**：重复渲染先删该 article 的旧行再登记新行；归档行
   （archived article）的 assets 目录与行保留不动，供回溯。
4. **图上文案不渲染 emoji**：PIL 默认字体不含彩色 emoji 字形，直接渲染会出豆腐块（违反
   "无乱码"验收）。渲染层统一剥离 emoji 与控制字符；emoji 只出现在标题与正文文案里。

## 【数据模型】

严格按计划书第 5 章，本阶段相关表 `articles / assets`，**不改表结构**。

- `assets` 字段（P-1a 已建）：`id / article_id / kind（cover | quote | data）/ path（相对
  data/ 的路径，如 `assets/42/01_cover.png`）/ width / height / created_at`。
- 出图对应关系：封面 → `kind=cover`；金句图 → `kind=quote`；`width/height` 按版式画布
  （1080×1440）如实登记。
- `articles.meta` 不变：`cover_note / image_plan` 沿用 P1 的写入；出图结果不回写 meta，
  以 assets 表为准（SDD 5.1：assets 挂文章）。
- 状态流转不变：出图失败沿用 `failed` 落行逻辑（同 LLM 失败、敏感词命中一个待遇）。

## 【验收标准】

计划书 P2 原文：**一篇笔记自动生成 1 张封面 + 2 张以上金句图；中文渲染无乱码无截断；
重复生成不产生残留文件。** 展开为可执行条目：

### 结构验收（mock 模式，无 Key 可跑通）

1. mock 下对 radar 选题调 `POST /api/topics/{id}/generate?platform=xhs`，成功后
   `data/assets/{article_id}/` 内含 `01_cover.png` + ≥ 2 张 `02_quote.png`…；
   `assets` 表行数与目录文件数一致，`kind / path / width / height` 正确。
2. 中文无乱码无截断：构造 > 40 字的金句用例，验证自动缩小字号而非截断（M7 规则）；
   长句自动折行；渲染前剥离 emoji 后不出现豆腐块。
3. 重复生成不残留：同 topic 再次 generate（开新行、新 article_id），新目录文件数与
   assets 行数一致；旧 article 的 assets 目录与行保留。
4. `wechat_cover` 版式独立渲染出 900×383 PNG（单元测试；调用方 M6 留后续阶段）。
5. `checklist_card` 版式独立渲染验证（单元测试，不接生成链路）。
6. 字体缺失路径：临时改名 `data/fonts/` 后 generate xhs 落 `failed`，error 提示字体
   缺失与放置方法。
7. `tests/test_p2.py` 可重复运行且通过；P0 / P1 回归（`test_p0.py` / `test_p1.py`）仍通过。

### 质量验收（人工目检）

三套小红书版式 + 公众号封面各渲一张样张：背景、槽位、字号层级、配色符合版式配置；
文字无贴边、无溢出。样张不入库，目检通过后在 README 记录版式清单。

## 【纪律】

- 不实现计划书第 2 章非目标，**尤其 AI 生图**：只用 PIL 模板合成，不引任何扩散模型依赖。
- **版式即数据**：画布、背景、字体、字号、槽位、色值全部在 YAML；代码里写死色值或字号即返工。
- 字体只从 `data/fonts/` 加载，**禁止依赖系统字体路径**（计划书 6.3）；FontLoader 进程内
  缓存，避免每张图重读字体文件（SDD 6.1）。
- 分层边界：出图入口在 `adapters/xhs.py`（调 imaging 服务并登记 assets），imaging 不含
  业务逻辑、不调 LLM、不调外部接口。
- 依赖克制：`requirements.txt` 只新增 `Pillow` 并锁版本，不引其他依赖。
- 输出目录、默认版式名、字号下限等常量集中 `config.py`，不散落模块。

## 【字体前置与无字体降级】（第 0 步，计划书 00 章硬前提）

- 开工第 0 步：下载**思源黑体 SourceHanSansSC**（OFL 授权）Regular + Bold 两个字重，
  放入 `data/fonts/`；先用 PIL 渲一张测试图确认中文无乱码，再动代码。
- 来源：https://github.com/adobe-fonts/source-han-sans/releases（OTF / TTF 均可；
  网络不稳时用镜像或国内 CDN）。`data/fonts/README.md` 记录版本、来源与授权。
- 无字体时的行为：imaging 初始化即抛出明确错误（"data/fonts/ 缺少字体文件，见
  docs/p2-task.md"），generate xhs 落 `failed` 并注明原因——不做静默跳过出图的降级，
  因为"有文案无图"违反 SDD 5.7 半成品禁令。
- 字体随仓库分发后，`tests/test_p2.py` 不依赖外网与系统字体即可重复运行。

## 【交付文件清单】

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `app/services/imaging.py` | 新建 | 共享图文服务：版式加载、字体缓存、换行与自适应字号、`render_note_images` / `render_wechat_cover` |
| `data/imaging_templates/emotion_cover.yml` | 新建 | 情绪封面版式（1080×1440） |
| `data/imaging_templates/quote_card.yml` | 新建 | 金句卡片版式（1080×1440） |
| `data/imaging_templates/checklist_card.yml` | 新建 | 清单卡片版式（1080×1440，仅独立渲染验证） |
| `data/imaging_templates/wechat_cover.yml` | 新建 | 公众号封面版式（900×383） |
| `data/fonts/` | 新建目录 | 思源黑体两字重 + 来源与授权 README |
| `app/adapters/xhs.py` | 修改 | 新增 `render_assets(article_id, cover_note, image_quotes)`：调 imaging 出图、清旧、登记 assets |
| `app/api/routes_topics.py` | 修改 | xhs 成功分支在落库事务内出图（失败 → `failed`） |
| `app/config.py` | 修改 | `FONTS_DIR / IMAGING_TEMPLATES_DIR / ASSETS_DIR`、默认版式名、字号下限 |
| `.gitignore` | 修改 | 白名单追加 `!data/fonts/`、`!data/imaging_templates/` |
| `requirements.txt` | 修改 | 新增 `Pillow` 并锁版本 |
| `tests/test_p2.py` | 新建 | 结构验收 1–7（mock 端到端 + 版式单测 + 字体缺失路径 + 回归） |
| `README.md` | 修改 | 进度更新为 P2；字体来源与版式清单说明 |

## 【验收脚本示意】

```bash
# 1. 生成（mock 模式）→ 自动出图
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
AID=$(sqlite3 data/app.db "SELECT id FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;")
ls "data/assets/$AID/"          # 应含 01_cover.png、02_quote.png、03_quote.png…
sqlite3 data/app.db "SELECT kind,path,width,height FROM assets WHERE article_id=$AID;"

# 2. 重新生成开新行 → 新目录干净、旧目录保留
curl -X POST "http://127.0.0.1:8000/api/topics/1/generate?platform=xhs"
AID2=$(sqlite3 data/app.db "SELECT id FROM articles WHERE platform='xhs' AND status='ready' ORDER BY id DESC LIMIT 1;")
ls "data/assets/$AID2/" | wc -l
sqlite3 data/app.db "SELECT article_id, COUNT(*) FROM assets GROUP BY article_id;"

# 3. 字体缺失路径
mv data/fonts /tmp/fonts_bak
curl -X POST "http://127.0.0.1:8000/api/topics/2/generate?platform=xhs"
sqlite3 data/app.db "SELECT status,error FROM articles WHERE platform='xhs' ORDER BY id DESC LIMIT 1;"
mv /tmp/fonts_bak data/fonts
```

## 【版式 YAML 契约（建议 schema，实现时可微调但须三套统一）】

```yaml
name: emotion_cover
canvas: { width: 1080, height: 1440 }
background: { color: "#1a1b25" }        # 或 image: bg.png（可选，相对本目录）
font:                                    # 文件名相对 data/fonts/
  regular: SourceHanSansSC-Regular.otf
  bold: SourceHanSansSC-Bold.otf
slots:
  - id: accent_bar
    type: rect
    box: { x: 90, y: 140, width: 120, height: 8 }
    color: "#4b3fe3"
  - id: headline
    type: text
    box: { x: 90, y: 200, width: 900, height: 520 }
    font: { size: 96, weight: bold, color: "#ffffff", line_height: 1.3 }
    align: center
    valign: middle
    shrink_to_fit: true                 # 超长自动缩字号而非截断（字号下限在 config）
```

三套小红书版式的差异建议：

| 版式 | 主槽位 | 风格要点 |
| --- | --- | --- |
| `emotion_cover` | 大号情绪主标题（cover_note，≤12 字） | 深底白字、强对比，顶部色条点缀 |
| `quote_card` | 单句金句（image_quotes 逐句，每句 ≤20 字） | 大留白、居中排版，底部小字领域署名槽 |
| `checklist_card` | 标题 + 多行清单槽（本阶段固定示例数据渲染） | 行首序号色块 + 分隔线，为后续清单类笔记预留 |
