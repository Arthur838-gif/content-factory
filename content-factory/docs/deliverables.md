# 交付总览（2026-08-17 ~ 2026-08-20）

> 项目从零到全链路跑通的完整交付账本，按时间线组织；每项附提交号可回溯
> （`git show <hash>`）。功能细节看 README 对应章节，阶段任务书与过程记录
> 见 `docs/` 其余文件。共 **54 个提交、4 个开发日**（08-17 ~ 08-20）。

## 里程碑一览

| 里程碑 | 一句话 | 代表提交 |
| --- | --- | --- |
| P-1a + P0 | 骨架与主干：8 张表 + 采集/雷达/告警/调度 + 选题→生成→落库 | 19f7a7d |
| P1 | 小红书文案：XhsNote 契约 + M7 适配 + 双端敏感词 | 499bb79 |
| P2 | 图文合成：共享 imaging 服务 + 版式即数据 + 出图契约 | e876d3d |
| P3 | 管理页与素材包：预览页 + ZIP 素材包 + 发布回填 + 模板管理页 | 70b73f4 |
| P-1b | 低粉爆款引擎：RedFox 采样 + 打分建题 + 周度拆解 + 熔断 | 9bcdf17 |
| P4 | 数据飞轮：回填驱动评分 + 模板效果分 + 成本报表 + 阈值校准 | e622a5d |
| P5/P5b | 内容栏目层：周排期 + 周主题制 + 系列联动 + 合集防虚构 | 69cdfbe / f4d2e45 |
| P-2 | 长期运行：Alembic 迁移 + 词表入库 + 持久化采样任务队列 | 368b65c |
| P6 | 标题六维打分 + 多平台改写（红狐 skill 方法论内化） | 90d4b4b |
| P7 | 合集荐真实工具：GitHub 开源项目采集器（时效优先） | d640428 |
| P5c | 模型配置页：文案/图片大模型运行时切换 | c206363 |
| P8a | 小红书链路增强：素材摘录 + 平台硬上限 + 违禁词体检回填 | 08fca48 |
| P9 | 公众号数据链路：优质库采样 + 阅读量判定 + 生成/封面/体检/回填闭环 | （本次提交） |

---

## 08-17：主干打通（P-1a / P0 / P1 / P2 / P3）

- **P-1a 基础设施**（随 19f7a7d 首批入库）：`models.py` 计划书 8 张表全量；
  collectors 基类（URL 去重、领域过滤、熔断器）+ RSSHub 热榜采集器；radar 服务
  （领域关键词过滤 + Jaccard 撞题去重 + 自动建题 + 过期归档 + 90 天清理）；
  notify（Server酱 webhook 告警）；APScheduler 调度器；admin API；`data/domains.yml` 种子。
  同批入库的还有计划书与 SDD 的 HTML 原件（`content-factory-plan/`）。
- **P0 生成主干**：generator（OpenAI 兼容协议 + 强制 JSON mode + 3 轮重试 +
  无 Key 自动 mock 降级 + 成文敏感词闸）、prompt_engine（模板库内版本化、
  种子幂等、启停热更新）、选题→生成→落库 HTTP API、`sensitive_wechat.txt`。
- **P1 小红书文案**（499bb79）：`XhsNote` 五字段契约（标题/正文/标签/封面语/金句）、
  `xhs_note.yml` 种子模板、M7 适配层（标签去重拼接 `#a #b`、meta 平台差异字段）、
  `sensitive_xhs.txt`。
- **P2 图文合成**（e876d3d）：共享 imaging 服务（画布/槽位/字体全在 YAML，
  改版式不重启）、思源黑体随仓库分发（缺失时 failed 不出乱码图）、
  articles 与 assets 同事务的出图契约、四套版式（emotion_cover 1080×1440 /
  quote_card / checklist_card / wechat_cover）。
- **P3 管理页与素材包**（70b73f4）：文章预览页（三区复制 + 图片区 + failed 全文）、
  小红书 ready 素材包 ZIP（title.txt / content.txt / images/NN_kind.png）、
  发布回填（追加记录 + published 终态）、模板管理页、目录穿越防护。

## 08-18：数据飞轮 + 栏目化 + 稳定性（P-1b / P4 / 评审 / P5 / P5b / P6 / P7）

- **P-1b 低粉爆款引擎**（9bcdf17）：RedFox 爆款洞察采集器（搜索自带 authorFans）、
  爆文率打分（(赞+2藏+3评)/粉）与低粉爆款判定、viral_samples 落库、自动建题、
  人工喂样本降级入口（与自动样本同管线）、周度 LLM 拆解（结论回写样本 + 标签库）、
  采集器熔断。fans 字段探针结论固化 `docs/p-1b-fans-probe.md`。
- **P4 数据飞轮**（e622a5d）：发布回填驱动 topics.score 重算（evidence 带
  base_score 快照、score 单调不降）、模板效果分（按模板聚合发布效果）、
  成本报表（按模型单价折算）、阈值校准视图。校准流程 `docs/p4-calibration.md`。
- **七维全项目评审修复**（431827f）：39 项，全部回归通过（`review-2026-08-18-fixes.md`）。
- **P5+A0 内容栏目层**（69cdfbe）：pillars 表（固定角度/每周期数/关键词池）、
  周排期（周更固定档幂等 + 多期轮换档绑素材）、栏目关键词池驱动采样、
  工作台页面重构（导航按工作流、概览卡、本周排期分组）。
- **模板 v2 栏目适配**（a5efa5e）：双模式人设（情绪共鸣/干货教程）、
  reference_points 素材引用块 + 反抄袭指令。
- **RedFox skills 调研**（34a0ab7 / 21283eb / fd74664 / d27e808）：113 个社区 skill
  挑 11 个付费实测 9 次——结论：违禁词检测可用（后于 P8a 接入），爆款雷达系
  不覆盖 AI 赛道，image-gen 仅支持付费积分；产出自有 GLM 生图 skill
  `skills/glm-cover-gen`（8880aae，cogview-4）。
- **P5b 周主题制**（f4d2e45 + 补丁 0182c1c / 3b4dcbd / 4ad00f1）：LLM 周主题规划
  （素材 <3 拒绝防编造）+ 子话题分期建题 + 生成注入系列上下文（期数/主题/合集枢纽）、
  合集素材不足拦截、按主题一键重排（归档旧选题）、主题生成 keepalive、
  归档选题不占档位、选题标题可编辑。
- **生成稳定性**：glm-4.7 思维链烧尽 max_tokens 致空串的修复（c468e94，围栏/夹文
  容错 + 截断明确报错）→ glm 系默认关思维链（b470f11，324→23 tokens 实测）；
  生成后返回工作台不刷新的缓存问题（8da1c52，no-store + bfcache 兜底）。
- **两段式封面上线**（4a46b04）：LLM 归纳画面提示词 → cogview-4 无字底图 →
  PIL 叠印中文标题（图像模型中文直出不可靠的实测结论）；任何失败回退纯色版式。
- **P6 标题打分 + 多平台改写**（90d4b4b）：六维加权打分（红狐原版权重，S/A/B/C
  分级）+ 以成文为源的跨平台改写（铁律禁止新增原文没有的信息）。
- **P7 GitHub 采集器**（d640428 + 时效修正 4786063）：只读 GitHub search，
  中文关键词映射英文查询，likes=star/collects=fork 入 hot_items；P7b 加 created
  窗口 + star 门槛降为 200 + pushed 30 天——窗口内 star 倒序即"涨星最快的新锐"。
  模板 v4 素材分流铁律（GitHub 素材只许介绍列出的项目）。
- **定时采样下线**（1c023c9）：RedFox 按调用计费，xhs 定时采样改 /viral 手动触发
  （`CF_XHS_SAMPLE_SCHEDULED` 可恢复）。
- **页面与词表运营**：采集器列表口径、工作台联动收尾（a0e53a6 / 8ea03b9 / bbd87f8）、
  选题「改标题」按钮移除但 API 保留（4ffcd03）、栏目改名（d0fa2b1）、
  词表补「文学」「职场成长」领域（230b96e / 3c53974）、栏目删除级联清理
  （532d19f / ad21b4c，已成文栏目 409 保护 + force 二次确认）。

## 08-19：长期运行基础设施 + 领域运营（P-2 等）

- **P-2 长期运行基础设施**（368b65c）：Alembic 程序化迁移（baseline → domains_jobs，
  启动自动升级）；领域词表从 yml 入库为唯一事实源（`/api/domains` 运行时改词表）；
  采样任务持久化队列（入队 202 + worker 领取执行 + 进度逐词落库 + 取消/重试 +
  服务重启续跑 + 租约回收）；测试与 CI 整固（run_all 13 套 + pytest 正式用例）。
- **RedFox 超时治理**（cb3412b → b067d17）：30s → 180s 慢响应上限 + 10s 连接快败
  （实测慢调用可超 30s，短超时误判网关故障）。
- **新建栏目自动定向采样**（41ecde6）：建栏目即按关键词池补素材 + 领域下拉
  （官方 24 类目）与关键词推荐；**领域发现**（be3ca6c / c8eff4c）：七日爆款榜
  推荐词 + 对标账号搜索，领域字段全部可选可填。
- **单栏目定向采样入口**（25447c0）：`POST /api/pillars/{id}/sample` + 行内按钮；
  周更固定档也能规划周主题（5d50db8）。
- **过滤修复**：关键词采样条目按采样词兜底入库（c69abd0，搜索结果标题未必含
  关键词，此前被误扔）；文学领域词表补全（d2d359d，金句摘抄类过不了入库过滤）。

## 08-20：收口与小红书链路增强

- **xiaohongshu-mcp 降级源废弃**（c6e0afe）：本地服务常年未部署，降级只会把
  RedFox 真实故障盖成「连接被拒绝」且搜索不含 fans——RedFox 成为唯一采样源。
- **P5c 模型配置页**（c206363）：`/models` 维护多套大模型配置（名称/base_url/
  api_key/模型名/单价/思维链开关），文案与图片各设一个「当前使用」，页面切换
  下一次生成即生效；`.env` OPENAI_* 降级为回退默认；`meta.usage.model` 记实际
  调用模型、成本按各配置单价归因；key 明文只存本地库、API/页面/日志一律掩码。
- **P5d 工作台直触采样按钮**（9d3b82d）：灵感选题区「采样一轮」——入队 → 轮询 →
  完成整页刷新，防重复计费 + 熔断提示，不跳 /viral。
- **P8a 小红书链路增强**（08fca48）：
  - 素材正文摘录（RedFox `raw.article.desc`）进 evidence 快照与 reference_points，
    提示词从「标题（链接）」升级为「标题（链接）：正文摘录」；GitHub 行自动去重；
    顺修 rstrip 剥掉链接右括号的老 bug。
  - XhsNote 平台硬上限：标题 ≤20 字、正文 ≤950 字（超限失败进重试自纠，
    免发布前手动剪）。
  - 违禁词体检：xhs ready 文章页按钮（confirm 后才调 RedFox，按调用计费）→
    命中词逐个回填本地 `sensitive_xhs.txt`（即时生效，滚动扩充）；英文子串
    误报剔除。真实验证：文章 37 检出「绝对 / 推荐」两个行业禁用词。
- **进度快照**（fb27c99）：挂起项清单与恢复路径（生图总闸、周度拆解、发布回填、
  手动建题入口、热榜数据源等，下周做）。

## 08-20（下午）：P9 公众号数据链路与生成发布体验补齐

- **接口选型**：12 份 RedFox 公众号 API 文档全读，只接优质库 2 个只读接口——
  `searchArticle`（sortType=_4 最热，采样核心）、`queryArticleDetail`（按 URL
  抓详情，手动喂样本）；账号维度 3 个（searchUser/queryWorkList/queryUser）、
  广域库 5 个、AI 创作搜索 2 个全部记入推迟项。
- **采样与判定**：`GzhSampleCollector`（gzh_sample）进统一采样队列（手动触发，
  定时仍仅 xhs——计费纪律）；阅读量口径判定
  `gzh_score = (likes + watches + 2×collects + 3×shares + 3×comments) ÷ max(reads,1)`，
  `reads ≥ 10000 且 score ≥ 0.08` 入选（无粉丝字段，不复制「低粉」；gzh 条目
  绝不走 xhs 判定——fans=0 会除以 1 全数误判，入库分流有守卫用例）。
- **手动喂样本（URL 抓取式）**：贴文章链接 + 领域 → 详情接口抓全量指标（1 次
  计费，confirm；URL 重复 409 先查重不白花）→ 同一建题管线。
- **生成质量**：WechatArticle 硬上限校验（30/54/3000 字）；wechat_article.yml
  v2（系列上下文 + 标签候选 + 公众号写作规范）；标题打分平台化（wechat 四维
  0-100 制 vs xhs 六维 0-10 制，同响应形状前端零改动适配）。
- **发布链路**：PIL 封面 900×383（零计费，摘要优先）+ 文稿素材包
  （title/digest/content/images）；违禁词体检平台参数化（wechat → 微信公众号
  词库）；回填表单分平台（公众号 阅读/在看/点赞/分享/收藏/评论）；效果分扩展
  `+ watches×1 + shares×3`（reads 是受众规模量纲不进效果分）。
- **真实接口验收**：`tests/_run_real_gzh_acceptance.py`（3 次计费，不进 CI）。
  08-20 执行：searchArticle ✓（sortType=_4、20 条全量指标）；违禁词
  platform=微信公众号 ✓（wechat 体检保持开放）；queryArticleDetail 接口通但对
  搜索长链 6/6 返回 3203「优质库暂未收录」（不扣费）——数据覆盖面问题，
  广域库 fallback 与 workUuid 查详情两条补救路径记入推迟项。首两轮遇 RedFox
  story API 全线 502（服务端故障，与参数无关），第三轮恢复后完成。
- **回归**：pytest 86 项（新增 test_gzh.py 32 项）+ `tests/run_all.py` 13 套
  全绿；test_p0/p1/p2/p3 中旧口径断言同步更新（v2 模板键、wechat 出图、
  双平台采样按钮、体检平台参数）。

---

## 质量基线（截至 08-20）

- **测试**：`tests/run_all.py` 13 套验收脚本 + pytest 86 项正式用例，全绿；
  离线可重复（LLM mock / 采样桩 / 临时库），真实付费调用一律不进测试。
- **纪律**（全程未破）：模板零 innerHTML（守卫用例）；api_key 只回掩码；
  RedFox 只读查询、按调用计费全走手动/任务队列；绑定 127.0.0.1，
  暴露局域网前先加认证；自动发布/自动互动为 Non-goals。

## 文档地图

| 文件 | 内容 |
| --- | --- |
| `README.md` | 功能总览、配置要点、各阶段验收方式 |
| `docs/p0~p4-task.md`、`docs/p-1b-task.md` | 各阶段任务书（验收口径） |
| `docs/progress-2026-08-18.md` / `progress-2026-08-20.md` | 过程快照与挂起项 |
| `docs/review-2026-08-18-fixes.md` | 七维评审 39 项修复记录 |
| `docs/p4-calibration.md` | 阈值校准流程 |
| `docs/p-1b-fans-probe.md` | fans 字段探针结论（mcp 废弃依据） |
| `content-factory-plan/` | 计划书与 SDD HTML 原件 |
