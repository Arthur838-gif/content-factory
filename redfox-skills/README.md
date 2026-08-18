# RedFox Agent Skills（试用区）

来源：https://github.com/redfox-data/redfox-community （113 个社区 skill 中按本项目工作流挑选的 11 个）。

⚠️ **全部为付费 API**（按次计费，约 ¥0.02/次），鉴权用环境变量 `REDFOX_API_KEY`
（请求头 `X-API-KEY`，与 content-factory 的 `REDFOX_API_KEY` 头是同一把 key、不同头名）。
key 只放在 `content-factory/.env`（已 gitignore），勿写入本目录任何文件。

## 选用清单与用途（对应文案工厂流程位置）

| Skill | 用途 | 流程位置 |
| --- | --- | --- |
| xiaohongshu-title-score | 爆款标题生成 + 六维评分（主题匹配/结构/利益/情绪/稀缺/合规） | 选题 → 标题优化 |
| xiaohongshu-note-analyzer | 基于爆款数据给文案多维度评分 + 改进建议 | 生成后、发布前质检 |
| xiaohongshu-prohibited-word | 违禁词检测 + 上下文替换建议 | 发布前合规 |
| xiaohongshu-cover | 同赛道爆款封面视觉分析 + 设计方案 | 出图环节参考 |
| xiaohongshu-lowtop | 低粉爆款榜（粉丝<5000 互动>500） | 低粉爆款引擎补充样本源 |
| xiaohongshu-ai-feed | AI 赛道小红书日报（聚类+HTML） | 栏目素材补充 |
| xiaohongshu-similar-account | 对标账号匹配（同阶可复制 + 高阶标杆） | 账号定位 |
| xiaohongshu-account-analyzer | 账号七维诊断 | 数据复盘 |
| wechat-title | 公众号爆款标题生成/评分 | 公众号线 |
| wechat-write | 公众号文案创作（爆款雷达驱动） | 公众号线 |
| gzh-search | 公众号文章搜索爬取（表格+CSV+HTML） | 公众号素材采样 |

## 结构约定

每个子目录一个 skill：`SKILL.md`（智能体提示词入口）+ `scripts/`（取数脚本）+
`references/` 等。用法：把 SKILL.md 交给支持 Agent Skills 的工具，或直接跑
`scripts/*.py`（脚本可独立运行，参数见各文件 argparse）。

## 数据端点（脚本实际调用的）

- `GET https://redfox.hk/story/api/cozeSkill/getXhsCozeSkillData` —— 四类爆款数据
  （低粉爆款 / 点赞 TOP500 / 单日爆发 / 7 日持续增长），title-score、note-analyzer、
  lowtop、ai-feed 等共用
- `GET https://redfox.hk/story/api/cozeSkill/sensitiveWordSearch` —— 违禁词

## 试用记录（2026-08-18，共 6 次付费调用，错误调用不扣积分）

| 端点 / skill | 结果 | 结论 |
| --- | --- | --- |
| sensitiveWordSearch（prohibited-word） | ✅ 对含风险文案正确标出 全网(敏感)/最牛(禁用)/百分百(禁用)/免费(行业禁用)/试用(禁用)，返回 HTML 标注 + 原文 | **值得用**：与赛道无关，发布前合规刚需，候选接入生成后质检 |
| getXhsCozeSkillDataLowFans（lowtop） | ⚠️ 端点正常（2026-08-16 综合全部 50 条、数码科技 50 条），但数据滞后约 2 天，数值为 "10w+" 字符串，fans 偶为 null；「数码科技」分类被游戏/娱乐内容占据，50 条中仅 1 条 AI 相关 | **暂不采用**：对 AI 赛道无覆盖；低粉爆款采样继续用 content-factory 已接入的爆款洞察关键词搜索（可按词检索 + authorFans，更适合） |
| image-gen（gpt-image-2 文生图/图生图，可做封面成图） | ❌ 提交任务即被拒（code 3203）：接口"仅支持付费调用"，免费积分不可抵扣，未产生扣费 | **无法试用**：需先在 redfox.hk 充值才能测；是否值得取决于充值意愿（gpt-image 单张成本显著高于检索类接口） |
| xiaohongshu-cover（封面设计方案） | ❌ 数据底座 getXhsCozeSkillData 对所有关键词返回空（见下） | **暂不采用**：取不到同赛道爆款封面，方案生成失去数据前提 |
| getXhsCozeSkillData（title-score / note-analyzer / ai-feed 数据底座） | ❌ 关键词「AI工具」「AI」「ai工具」「AI工具,效率工具,AI教程」及对照组「穿搭」（含加 startDate 复测）均返回空（code 2000 但四类榜单 0 条）——端点本身无数据，与赛道无关 | **暂不采用**：该端点已实质不可用；评分/生成等纯提示词环节失去数据前提 |

未实测：similar-account / account-analyzer / wechat 系列（需要时再按次试用）。
key 复用 content-factory/.env 的 CF_REDFOX_API_KEY；本目录所有文件均不含密钥。
