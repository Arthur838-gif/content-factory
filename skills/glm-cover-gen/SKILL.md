---
name: glm-cover-gen
description: 用 GLM（智谱 cogview-4）生成小红书封面图与配图。当用户需要为笔记/文章生成封面插画、配图、风格图时使用。密钥复用 content-factory/.env 的 OPENAI_API_KEY。
---

# GLM 封面/配图生成

自有 skill：走智谱 OpenAI 兼容接口的 `cogview-4` 文生图，单张约 ¥0.06，
密钥复用文案工厂的 GLM key（`content-factory/.env` 的 `OPENAI_API_KEY`），
不依赖 RedFox。

## 快速使用

```bash
python skills/glm-cover-gen/scripts/glm_imagegen.py "提示词"                 # 小红书 3:4 封面
python skills/glm-cover-gen/scripts/glm_imagegen.py "提示词" --size square  # 1:1 方图
```

尺寸预设：`xhs-cover`（864x1152，默认）/ `xhs-full`（768x1344）/ `square` /
`wide`（1440x720）/ `banner`（1344x768），也可直接传 `WxH`。
其他参数：`-n` 张数、`--out` 目录、`--prefix` 文件名前缀、`--model`
（默认 cogview-4，可用环境变量 `GLM_IMAGE_MODEL` 换 cogview-3-flash 等免费档）。

## 封面制作建议（重要）

cogview 对**中文大字标题的渲染不可靠**（易错字/变形）。推荐两段式：

1. 用本 skill 生成**无文字的背景插画**（提示词里不要要求写字）；
2. 用 content-factory 的 imaging 模块（PIL 版式）把标题/金句**叠印**上去，
   文字 100% 可控，且与现有出图流程一致。

若整图直出（含文字），先小规模验证文字效果再用。

## 提示词要点（小红书封面向）

- 风格词：扁平插画 / 3D 渲染 / 手绘涂鸦 / 渐变科技感，高饱和、明快
- 构图：主体居中偏上、底部留白（给叠字留位置）
- 赛道示例（AI工具号）：`"明亮渐变背景（紫到蓝），中央扁平插画的小机器人
  助手与漂浮的工具图标，现代科技感，干净排版，底部三分之一留白"`
