# 全项目评审修复记录（2026-08-18）

针对七维评审（功能正确性 / 可读性 / 安全 / 性能 / 可维护性 / 健壮性 / 测试）提出的全部问题的修复清单。
修复后 7 个验收脚本（test_p0 ~ test_p4）全部 PASS，新增 14 项冒烟分支验证全部 PASS。

## 一、数据正确性（运营前必修，4 项全修）

1. **hot_items 90 天清理的外键冲突**（`radar.cleanup_hot_items`）：被 `viral_samples` 引用的行跳过删除
   （样本永久保留契约 + PRAGMA foreign_keys=ON 强删会回滚整轮清理），并记日志说明跳过数。
2. **撞题合并与 P4 重算的基线失同步**（`radar.create_or_merge_topic`）：合并抬高 `score` 时同步抬高
   `evidence["base_score"]` 快照，防止下次全量重算把合并进来的分数打回旧基线（score 单调不降契约）。
3. **发布回填零校验**（`routes_articles.publish_article`）：
   - 新增 `PublishMetrics` 模型（likes/collects/comments 非负、≤10^9，extra=allow）；
   - 非法 status（archived 等）回填 → 409；回填 platform 与文章不一致 → 422。
4. **热榜全源失败不触发熔断**（`hotboard.HotboardCollector.fetch`）：全部源失败时 raise，
   让 `run_collector` 计失败数并驱动熔断器（此前空列表被记为 success）。

## 二、错误处理与健壮性

5. 单源失败告警风暴（`FailureTracker`）：达到阈值只告警一次，成功后重新武装。
6. LLM 重试无退避、4xx 也重试（`generator._real_generate`）：固定退避 `CF_LLM_RETRY_BACKOFF_SECONDS`（默认 2s）；
   4xx（429 除外）不可重试，直接失败止损。
7. `usage["__error"]` 魔法键（`generator`）：重构为 `(article, usage, error)` 三元返回，调用方不再 pop 魔法键。
8. 周度拆解被 xhs 敏感词误杀（`radar.run_weekly_teardown`）：`generate(check_sensitive=False)` 显式豁免
   （拆解产物是模式总结非发布文案，唯一豁免点）。
9. imaging：`load_template` 校验 canvas width/height；`render_note_images` 把意外异常（裸 KeyError 等）
   统一收敛为 `ImagingError`（调用方 failed 落库而非 500）；渲染后 `img.close()`；消除二次 `load_template`。
10. SQLite 并发写锁（`db.get_engine`）：`connect_args.timeout=30`（busy timeout）。
11. 人工喂样本并发竞态（`routes_viral_samples`）：URL 唯一约束 `IntegrityError` → 409（原先 500）。
12. prompt_id 跨场景套用（`prompt_engine.select_prompt`）：platform/scenario 不匹配 → 409（原先渲染层 500）。
13. 模板标记误判（`prompt_engine._split_template`）：仅 `#` 开头的行可作 system/user 标记。
14. admin 502 泄漏 `repr(exc)`：改为「异常类型 + 截断摘要」。
15. 调度器六段重复 try/except：统一 `_scheduled_job(module, event)` 装饰器（CircuitOpenError 仍由 job 内跳过）。

## 三、安全加固

16. Host/Origin 白名单中间件（`main.create_app`）：只放行 127.0.0.1 / localhost / ::1 / testserver
    （防 DNS rebinding 与跨站写；TestClient 默认 Host=testserver 故测试不受影响）。
17. RSS 解析防护（`hotboard.parse_rss`）：拒绝 DTD/实体声明 + 报文体积上限 `RSS_MAX_XML_CHARS`（xml.etree 无实体展开防护）。
18. 输入上限：`ManualSampleInput` 长度/量纲上限；`PromptCreate.template` ≤64KB；`PublishRequest` 字段上限。
19. viral 页样本外链 scheme 白名单（http/https 才可点击，防 javascript: 注入）。
20. `.gitignore` 追加 `*.log`（alert_received.log 确认未被跟踪）。

## 四、性能

21. `persist_hot_items` 整批只读一次领域词表（原先每条目读一次 domains.yml）。
22. 撞题 Jaccard 候选分词加 `lru_cache`（一轮采集内候选标题不再重复分词）。
23. 校准视图 summary 改为全量统计（展示列表截 200，汇总不再被 limit 失真；并注明万级样本时下推 SQL）。
24. 列表接口分页：`GET /api/topics`、`GET /api/articles` 增加 limit/offset。
25. hotboard 共享 `httpx.Client`（原先每源每轮新建连接）。
26. 成本口径剔除 model=mock 行（不再稀释平均单篇成本）。

## 五、可维护性（DRY / 枒构）

27. 三处重复的样本联表查询统一为 `radar.query_viral_samples()`。
28. 手动任务伪注册表 `_TEARDOWN_TASKS` 收敛进 `collectors.base` 的 `MANUAL_TASKS`（routes_admin 只登记）。
29. `/api/prompts/stats` 从 routes_stats 移到 routes_prompts（资源归属，URL 不变）。
30. `config.TEMPLATES_DIR` 集中管理页面模板目录；pages 复用 `_prompt_dict` 序列化。
31. 月份校验统一 `scoring.normalize_month`（API 与页面同口径，"9999-99" 一律 422）。
32. 删除死代码 `system_msg, user_msg = system_msg, user_msg`；删除 xhs_sample 过期的"避免导入环"注释与延迟导入。
33. cover_text 为空时封面兜底用文章标题（保证素材包首图恒为 01_cover）。
34. stats.html 阈值数字由后端下发（不再硬编码 10）；article.html 文件名由后端 `filename` 字段给出；
    prompts.html 启停/创建失败给出明确告警。
35. 新增 `pyproject.toml`（ruff 配置，不引入依赖；依赖仍以 requirements.txt 为准）。

## 六、测试

36. `tests/conftest.py`：pytest 环境下跳过七个独立验收脚本（模块级改 config，被收集会互相污染）。
37. test_p2「旧目录保留」恒真断言改为精确校验（脏文件 junk.txt 必须仍在）。
38. test_p3 运算符优先级 `A or (B and C)` 补括号。
39. 冒烟新增 14 项分支验证（发布校验、终态契约、跨场景模板、清理跳过、月份校验、Host/Origin、页面渲染）。

## 遗留（非代码项，需运营动作）

- GLM 凭据轮换（早前聊天泄漏的 Key 作废）。
- `tests/_run_real_acceptance.py` 决定入库或删除（当前未跟踪）。
- P-1b 连续 3 天真实采样、首次真实发布回填、Golden Set 固化。
- 阈值校准仍为初值，待回填数据累积后按 docs/p4-calibration.md 流程拍板。
