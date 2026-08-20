<div align="center">

# 文案工厂 · Content Factory

**公众号 + 小红书双端内容流水线**：多源采样 → 爆款判定建题 → AI 生成 → 图文素材包 → 发布回填 → 数据飞轮

[![Tests](https://github.com/Arthur838-gif/content-factory/actions/workflows/tests.yml/badge.svg)](https://github.com/Arthur838-gif/content-factory/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)

[📖 操作指南](content-factory/docs/操作指南.md) · [🛠 工程文档](content-factory/README.md) · [📋 开发计划书](content-factory-plan/content-factory-plan.html) · [🧭 SDD](content-factory-sdd.html)

</div>

---

## 它解决什么问题

一个人同时运营公众号和小红书，最耗时的不是写，而是**找题、仿写、复盘**。本项目把这条流水线自动化到「人工只做判断和发布」：

```
栏目词池 ──▶ 多源采样 ──▶ 爆款判定 ──▶ 选题（灵感 + 周排期）
                │                            │
                │                            ▼
                │                 LLM 生成（平台字数校验 + 标题打分）
                │                            │
                │                            ▼
                │              PIL 封面 / 素材包 / 违禁词体检
                │                            │
                ▼                            ▼
             数据飞轮 ◀── 评分重算 / 模板效果分 / 成本报表 ◀── 人工发布回填
```

发布与回填永远由人工发起，系统不碰任何账号写操作。

## ✨ 功能一览

- **多源采样**：RedFox 小红书爆款洞察、公众号优质库、GitHub 新锐项目（免鉴权）、微博/知乎/百度热榜（免费）；按调用计费的接口全部手动触发 + 二次确认，任务级去重防误触
- **爆款判定自动建题**：小红书「低粉爆款」口径（粉丝 ≤5000 且互动密度 ≥2.0），公众号「阅读量 + 互动密度」口径；纯规则实时判定，阈值环境变量可热改
- **内容栏目与周排期**：栏目关键词池定向采样 → 周主题 + 子话题 → 排期槽位，选题撞题自动合并（标题 Jaccard ≥0.5）
- **双端生成**：小红书笔记（标题 ≤20 字 / 正文 ≤950 字）与公众号长文（标题 ≤30 / 摘要 ≤54 / 正文 ≤3000 字）；超限自动重试自纠，标题六维/四维打分
- **图文合成**：PIL 版式封面 + 金句卡（版式即数据，画布/字体/槽位全在 yml）；可选 GLM cogview-4 生成底图再叠字
- **一键素材包**：文章页下载 ZIP（title/content/images），封面、配图按序编号，直接对照发布
- **违禁词体检**：RedFox 违禁词库检测标题 + 正文，命中词一键回填本地词表，下次生成前拦截
- **数据飞轮**：发布回填 → 选题评分自动重算 → 模板效果分（按版本聚合）→ 分模型成本报表 → 阈值校准视图
- **长期运行**：持久化采样任务 + 可恢复 worker、Alembic 数据库迁移、每日备份轮换、采集器熔断与告警、GitHub Actions 离线 CI

## 🚀 快速开始

```bash
git clone https://github.com/Arthur838-gif/content-factory.git
cd content-factory

python -m venv .venv
# Windows（Git Bash）：
.venv/Scripts/pip install -r requirements-dev.txt
# Linux / macOS：
.venv/bin/pip install -r requirements-dev.txt

cp .env.example .env    # 填入 REDFOX_API_KEY / OPENAI_API_KEY
.venv/Scripts/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 **http://127.0.0.1:8000/** 即进入选题台。启动自动完成：数据库迁移、领域词表与提示词种子导入、定时任务、内嵌采样 worker。

> 未配 LLM Key 时自动走 mock（可跑通全链路结构）；RedFox 采样与违禁词体检需要真实 Key（按调用计费）。

## 🖥 页面一览

| 页面 | 路径 | 用途 |
| --- | --- | --- |
| 工作台 | `/` | 灵感选题、本周栏目排期、一键采样/生成 |
| 内容栏目 | `/pillars` | 栏目 CRUD、关键词池、周主题、定向采样 |
| 素材采样 | `/viral` | 手动采样、爆款样本、人工喂样本、任务进度 |
| 文章页 | `/articles/{id}` | 预览、重新生成、打分、体检、素材包、发布回填 |
| 模板管理 | `/prompts` | 提示词版本管理与启停 |
| 模型配置 | `/models` | 多套 LLM 配置、文案/图片模型一键切换 |
| 报表 | `/stats` | 成本、模板效果分、阈值校准 |

## 📂 目录结构

```
content-factory/
├── app/
│   ├── api/            # 路由：选题 / 栏目 / 采样 / 文章 / 模板 / 模型 / 报表
│   ├── collectors/     # 采集器：热榜 / RedFox 小红书·公众号 / GitHub
│   ├── services/       # 业务：雷达判定 / 生成 / 图文合成 / 评分 / worker / 调度
│   ├── adapters/       # LLM 与出图适配（OpenAI 兼容协议）
│   └── templates/      # Jinja2 管理页
├── data/               # 数据库 / 素材 / 字体 / 版式配置（不入库）
├── docs/               # 操作指南 / 交付记录 / 阈值校准
├── migrations/         # Alembic 迁移（schema 变更一律走这里）
├── prompts/            # 提示词模板（yml，版本化种子，页面可管）
└── tests/              # pytest + 离线验收脚本
```

## 🔑 环境变量（节选）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `REDFOX_API_KEY` | RedFox 数据源 Key（采样 / 体检必需） | — |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MODEL_NAME` | 文案 LLM（OpenAI 兼容协议） | DeepSeek |
| `CF_VIRAL_FANS_MAX` / `CF_VIRAL_SCORE_MIN` | 小红书低粉爆款阈值 | 5000 / 2.0 |
| `CF_GZH_READS_MIN` / `CF_GZH_SCORE_MIN` | 公众号爆款阈值 | 10000 / 0.08 |
| `CF_XHS_SAMPLE_SCHEDULED` | 小红书定时采样（默认关，计费纪律） | false |
| `CF_LLM_MOCK` | 强制 mock（调试） | 0 |

完整清单见 [`content-factory/.env.example`](content-factory/.env.example)（带注释）。

## 🧪 测试与 CI

```bash
.venv/Scripts/python -m pytest tests -q        # 正式用例
.venv/Scripts/python tests/run_all.py          # 全量离线回归（pytest + 验收脚本）
```

GitHub Actions 全程离线（LLM mock / RedFox 打桩），不消耗任何付费 API；真实质量验收脚本保持手动运行。

## 📖 文档导航

| 文档 | 内容 |
| --- | --- |
| [操作指南](content-factory/docs/操作指南.md) | 日常使用手册：启动、采样、生成、发布回填、计费清单、FAQ |
| [工程文档](content-factory/README.md) | 各阶段设计与实现细节、验收口径、Non-goals |
| [开发计划书](content-factory-plan/content-factory-plan.html) | 上游事实来源 v1.3 |
| [软件设计文档](content-factory-sdd.html) | 模块与数据流设计 |
| [交付总览](content-factory/docs/deliverables.md) | 按时间线的全部交付记录 |

## ⚠️ 安全与边界

- **只读数据接口**：不做任何自动关注 / 点赞 / 评论 / 私信 / 发布，采样期间严禁账号互动
- **计费纪律**：RedFox 按调用计费，所有计费点人工触发 + 页面二次确认，真实调用不进 CI
- **只绑 127.0.0.1**：暴露到局域网 / 公网前必须先加认证
- **密钥只在 `.env`**（已 gitignore），代码 / 日志 / 页面一律掩码

明确不做的方向（自动化发布、矩阵养号、自动互动等）见工程文档 [Non-goals](content-factory/README.md#non-goals明确不做计划书第-2-章)。

---

<div align="center">

个人内部工具 · 未附加开源协议 · [反馈问题](https://github.com/Arthur838-gif/content-factory/issues)

</div>
