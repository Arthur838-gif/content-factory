# 获取小红书作品内容详情 (优质库)

查询作品信息

**`POST`** `https://redfox.hk/story/api/xhsUser/queryWorkDetail`

---

## API 说明

**Method**: `POST`
**Host**: `https://redfox.hk`
**Path**: `/story/api/xhsUser/queryWorkDetail`

---

## 请求头

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| REDFOX_API_KEY | string | 是 | 平台鉴权令牌，每次请求必填 | ak_xxxxxx |
| Content-Type | string | 是 | 请求体数据类型 | application/json |

---

## 请求参数

| 参数 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| workId | String | 否 | 作品id | 6a03be1b0000000035033163 |
| workLink | String | 否 | 作品链接 | https://www.xiaohongshu.com/explore/6a2ac3020000000035022d8e |

---

## 返回值与结构

统一包装一般为 `code`、`message`/`msg`、`data`（以实际服务为准）。

---

## 响应字段

| 字段 | 类型 | 说明 | 示例 |
| --- | --- | --- | --- |
| code | Integer | 状态码（2000=成功） | 2000 |
| msg | String | 提示信息 | 成功 |
| data | Object | 返回数据 | — |
| accountNickname | String | 作者昵称 | 大白萝不怪 |
| accountUserid | String | 作者小红书id | 565b17dc0bf90c754d6615b4 |
| coverUrl | String | 封面地址 | https://sns-i10.rednotecdn.com/notes_pre_post/1040g3k031k6lpmg43q2043gri3bto5dk2u1p6eg?imageView2/2/w/576/format/webp/q/87%7CimageMogr2/strip&redImage/frame/0&ap=1&sc=PREVIEW&sign=b670a0755ba8943337e700df1b2f702d&t=6a05685d&src=A |
| workCollectedCount | Integer | 收藏数 | 175 |
| workCommentsCount | Integer | 评论数 | 45 |
| workDesc | String | 作品内容 | 做了十多年建筑设计 标准化设计是硬性要求\n难得自己当甲方 不想把设计模版套在自己的家\n自己的房子总算能随心所欲\n家里的布置随着时间调整 越住越舒适~\n以舒适健康为居 是我们理想中高智感的家\n\t\n特别满意我的开放式厨房 洗切炒一气呵成\n要说不满意 就是用水问题\n武汉自来水氯味重得吓人\n做饭都闻到异味 污染事件更是频发\n拖了很久决定装净水器\n闺蜜家装的RO反渗透净水器虽然过滤效果好\n但每次制水要产生大量废水\n在业主群里咨询 |
| workId | String | 作品id | 687df3a1000000000d0184a4 |
| workLikedCount | Integer | 点赞数 | 210 |
| workPublishTime | String | 发布时间 | 2025-07-21 17:09:42 |
| workSharedCount | Integer | 转发数 | 28 |
| workTitle | String | 作品标题 | 建筑师的选择｜厨下净水器终于装好了！ |
| workType | String | 分类（视频/图文） | 主要描述：视频 or 图文 等 {"normal": "normal", "video": "video"} |
| workUrl | String | 作品链接 | https://www.xiaohongshu.com/explore/687df3a1000000000d0184a4 |

---

## 请求示例

```bash
curl -X POST "https://redfox.hk/story/api/xhsUser/queryWorkDetail"
  -H "Content-Type: application/json"
  -H "REDFOX_API_KEY: your_api_key"
  -d '{"workId": "6a03be1b0000000035033163", "workLink": "https://example.com/example"}'
```

---

## 响应示例

```json
{
  "code": 2000,
  "msg": "成功",
  "data": {
    "workId": "687df3a1000000000d0184a4",
    "workPublishTime": "2025-07-21 17:09:42",
    "workTitle": "建筑师的选择｜厨下净水器终于装好了！",
    "workDesc": "做了十多年建筑设计 标准化设计是硬性要求\n难得自己当甲方 不想把设计模版套在自己的家\n自己的房子总算能随心所欲\n家里的布置随着时间调整 越住越舒适~\n以舒适健康为居 是我们理想中高智感的家\n\t\n特别满意我的开放式厨房 洗切炒一气呵成\n要说不满意 就是用水问题\n武汉自来水氯味重得吓人\n做饭都闻到异味 污染事件更是频发\n拖了很久决定装净水器\n闺蜜家装的RO反渗透净水器虽然过滤效果好\n但每次制水要产生大量废水\n在业主群里咨询",
    "coverUrl": "https://sns-i10.rednotecdn.com/notes_pre_post/1040g3k031k6lpmg43q2043gri3bto5dk2u1p6eg?imageView2/2/w/576/format/webp/q/87%7CimageMogr2/strip&redImage/frame/0&ap=1&sc=PREVIEW&sign=b670a0755ba8943337e700df1b2f702d&t=6a05685d&src=A",
    "accountNickname": "大白萝不怪",
    "accountUserid": "565b17dc0bf90c754d6615b4",
    "workCommentsCount": 45,
    "workLikedCount": 210,
    "workCollectedCount": 175,
    "workReadedCount": 980,
    "workSharedCount": 28,
    "workUpdateTime": "2026-01-01 00:00:00",
    "workUrl": "https://www.xiaohongshu.com/explore/687df3a1000000000d0184a4",
    "workType": "主要描述：视频 or 图文 等 {"normal": "normal", "video": "video"}"
  }
}
```

---

## 密钥获取与安全说明

- 本API需要使用API密钥 `REDFOX_API_KEY`。
- API密钥由 [红狐 hub](https://redfox.hk/settings/api-keys?source=redfox_api_md) (`https://redfox.hk`)提供。
- 请前往 [红狐 hub](https://redfox.hk?source=redfox_api_md) 注册并登录账号，在密钥管理模块创建 API密钥。
- 复制并仅在请求头中使用API密钥。
- 在提供密钥前，请先确认密钥来源、可用范围、有效期及是否支持重置/撤销。
- 禁止在代码、提示词、日志或输出文件中硬编码/明文暴露密钥。
