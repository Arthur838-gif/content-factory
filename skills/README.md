# 自有 Skills（不走 RedFox，用自己的 GLM key）

| Skill | 用途 | 成本 |
| --- | --- | --- |
| glm-cover-gen | GLM cogview-4 文生图（小红书封面底图/配图） | 约 ¥0.06/张（cogview-4；cogview-3-flash 免费） |

- 鉴权复用 `content-factory/.env` 的 `OPENAI_API_KEY`（脚本自动读取，也可用环境变量覆盖）。
- 2026-08-18 实测：864x1152 竖版封面一次成功（约 20 秒，实际返回 JPEG，脚本已按魔数自动定后缀）。
- 封面推荐两段式：本 skill 出无文字底图 → content-factory imaging 模块 PIL 叠印标题（中文大字直出不可靠）。
