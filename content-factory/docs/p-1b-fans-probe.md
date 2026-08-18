# P-1b 第 0 步 · fans 字段探针记录

> 结论必须先于自动采样路径决策（任务四件套【目标】1）。

## 探针方法

1. 按 xpzouying/xiaohongshu-mcp 的 Go 独立服务方式用 Docker 起在本机
   （默认 `http://localhost:18060`，streamable-http 端点 `/mcp`），
   使用**专用数据小号**扫码登录（与发布账号物理隔离，凭据不进仓库）。
2. 确认服务健康：`curl --noproxy '*' -s http://localhost:18060/health`
3. 执行只读探针（仓库内置，等价于一次 search_notes 调用并检查返回字段）：

   ```bash
   .venv/Scripts/python -m app.collectors.xhs_sample probe "AI工具"
   ```

4. 判定标准：返回 JSON 的 `fans_available` 为 `true`（笔记/作者对象存在
   `fans` / `fans_count` / `fan_count` / `follower_count` 任一键且值 > 0）
   → 自动模式；否则 → 降级模式。

## 当前结论（2026-08-18，开发环境）

| 项 | 值 |
| --- | --- |
| `fans_available` | **false（未实测——本开发环境未部署 xiaohongshu-mcp，无专用数据小号）** |
| 采样路径 | **降级模式**：自动采样只落 `hot_items` 笔记级数据（likes/collects/comments），低粉爆款经 `POST /api/viral-samples/manual` 人工补齐 fans 后进入同一打分、落库、撞题与建题管线 |
| 代码侧 | 两条路径已同时实现且自动分流：条目 fans > 0 即参与低粉爆款判定（自动模式无感切换）；fans = 0 只落笔记级数据，不伪造、不判定 |

依据任务契约「无 mcp / 无小号降级方案」：探针不可用不阻塞代码验收，
结构验收用 mock/录制响应 + 人工喂样本完成；真实环境部署 mcp 后重跑
上述探针命令，把结论更新到本文件即可，代码无需改动。

## RedFox 双源结论（2026-08-18 晚，真实 Key 实测）

`python -m app.collectors.redfox probe "AI工具"` → **50 条笔记、50 条带 authorFans，
`fans_available: true`**。洞察接口（`/story/api/xhs/search/search`）响应与文档
一致（顶层裸给、无 code 包装），字段映射按 `tests/test_redfox.py` 固化。

真实采样一轮（`CF_XHS_SAMPLE_KEYWORDS=AI工具`）：fetched=50 → inserted=50 →
**viral_created=3、topics_created=3**（fans 662–4061、likes ~2000，
viral_score 2.49–9.81）。低粉爆款引擎首次端到端跑通，不再依赖 mcp 登录态。

采样优先级（`xhs_sample` 双源）：RedFox（配 Key）→ 无 Key / 调用失败降级
xiaohongshu-mcp（其 search_feeds 仍无 fans，走人工喂样本降级模式）。
`probe` 测 RedFox；`probe-mcp` 测 mcp。

## 纪律提醒

- 探针与采样只允许只读接口（search_notes）；禁止关注、点赞、评论、
  私信、回关、发布等任何账号行为。
- Cookie、二维码截图、账号信息只进本地 mcp 服务与操作员私有记录，
  不进 Git、不进聊天回复、不进测试快照。
