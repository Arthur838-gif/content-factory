# 文案工厂（content-factory）

公众号 + 小红书双端内容工厂：RedFox 采样 → 爆款判定建题 → LLM 生成 → 素材包 → 发布回填 → 数据飞轮。

- **日常使用**：[`content-factory/docs/操作指南.md`](content-factory/docs/操作指南.md) —— 启动、采样、生成、发布回填、计费清单、FAQ
- **工程与实现文档**：[`content-factory/README.md`](content-factory/README.md)
- **上游计划文档**：`content-factory-plan/`（开发计划书 v1.3）、`content-factory-sdd.html`（软件设计文档）
- **应用代码**：`content-factory/`（FastAPI + Jinja2 + SQLite，启动后访问 http://127.0.0.1:8000/）

密钥只存在本地 `content-factory/.env`（不入库）；服务只绑 127.0.0.1。
`redfox-api文档/`、`redfox-skills/`、`.idea/` 等为本地参考资料与 IDE 配置，保留在磁盘但不入库。
