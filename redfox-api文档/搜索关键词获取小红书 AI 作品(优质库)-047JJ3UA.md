# 搜索关键词获取小红书 AI 作品(优质库)

查询信息

**`POST`** `https://redfox.hk/story/api/parseWork/queryXhsAiMsgs`

---

## API 说明

**Method**: `POST`
**Host**: `https://redfox.hk`
**Path**: `/story/api/parseWork/queryXhsAiMsgs`

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
| keyword | String | 是 | 关键词 | AI |
| pageNum | Integer | 否 | 页码 | 1 |
| pageSize | Integer | 否 | 每页条数 | 20 |
| startTime | String | 是 | start时间 | 2026-06-01 00:00:00 |
| endTime | String | 是 | 结束时间 | 2026-06-02 00:00:00 |

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
| list | Array | 数据列表 | — |
| authorId | String | 作者id | 10000123456789 |
| commentCount | Integer | 评论数 | 100 |
| coverUrl | String | 图片地址 | https://sns-i10.rednotecdn.com/notes_pre_post/1040g3k031k6lpmg43q2043gri3bto5dk2u1p6eg?imageView2/2/w/576/format/webp/q/87%7CimageMogr2/strip&redImage/frame/0&ap=1&sc=PREVIEW&sign=b670a0755ba8943337e700df1b2f702d&t=6a05685d&src=A |
| gmtCreate | String | 创建时间 | 2026-01-01 00:00:00 |
| gmtModified | String | 修改时间 | 2026-01-01 00:00:00 |
| likeCount | Integer | 点赞数 | 100 |
| photoId | String | 作品id | 10000123456789 |
| platform | Integer | 平台 | example |
| shareCount | Integer | 分享数 | 100 |
| title | String | 标题 | 示例标题 |
| topic | String | 话题 | — |
| type | String | 类型 | default |
| url | String | 链接地址 | https://example.com/example |
| userHeadUrl | String | 作者头像 | https://example.com/example |
| userName | String | 作者名称 | 示例用户 |
| pageNum | Integer | 页码 | 1 |
| pages | Integer | pages | 25 |
| pageSize | Integer | 每页条数 | 20 |
| total | Long | 总数 | 100 |

---

## 请求示例

```bash
curl -X POST "https://redfox.hk/story/api/parseWork/queryXhsAiMsgs"
  -H "Content-Type: application/json"
  -H "REDFOX_API_KEY: your_api_key"
  -d '{"keyword": "示例关键词", "pageNum": 1, "pageSize": 20, "source": "示例值", "startTime": "2026-01-01 00:00:00", "endTime": "2026-01-01 00:00:00"}'
```

---

## 响应示例

```json
{
  "code": 2000,
  "msg": "成功",
  "data": {
    "list": [
      "photoId": "10000123456789",
      "authorId": "10000123456789",
      "coverUrl": "https://sns-i10.rednotecdn.com/notes_pre_post/1040g3k031k6lpmg43q2043gri3bto5dk2u1p6eg?imageView2/2/w/576/format/webp/q/87%7CimageMogr2/strip&redImage/frame/0&ap=1&sc=PREVIEW&sign=b670a0755ba8943337e700df1b2f702d&t=6a05685d&src=A",
      "userName": "示例用户",
      "userHeadUrl": "https://example.com/example",
      "title": "示例标题",
      "platform": example,
      "url": "https://example.com/example",
      "likeCount": 100,
      "commentCount": 100,
      "shareCount": 100,
      "readCount": 100,
      "type": "default",
      "topic": "示例值",
      "gmtCreate": "2026-01-01 00:00:00",
      "gmtModified": "2026-01-01 00:00:00"
    ],
    "total": 100,
    "pageNum": 1,
    "pageSize": 20,
    "pages": 25
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
