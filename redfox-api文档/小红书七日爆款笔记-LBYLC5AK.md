# 小红书七日爆款笔记

getXhsCozeSkillDataSeven

**`GET`** `https://redfox.hk/story/api/cozeSkill/getXhsCozeSkillDataSeven`

---

## API 说明

**Method**: `GET`
**Host**: `https://redfox.hk`
**Path**: `/story/api/cozeSkill/getXhsCozeSkillDataSeven`

---

## 请求头

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| REDFOX_API_KEY | string | 是 | 平台鉴权令牌，每次请求必填 | ak_xxxxxx |

---

## 请求参数

| 参数 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| rankDate | String | 否 | 榜单日期（每天 19:00 更新「昨日」榜单） | 2026-08-10 |
| category | String | 否 | 综合全部、出行代步、医疗保健、休闲爱好、综合杂项、婚庆婚礼、居家装修、影视娱乐、星座情感、拍摄记录、学习教育、旅行度假、亲子育儿、日常生活、科学探索、数码科技、时尚穿搭、化妆美容、个人护理、美味佳肴、职业发展、宠物天地、新闻资讯、体育锻炼、潮流鞋包 | 综合全部 |

---

## 返回值与结构

统一包装一般为 `code`、`message`/`msg`、`data`（以实际服务为准）。

---

## 响应字段

| 字段 | 类型 | 说明 | 示例 |
| --- | --- | --- | --- |
| code | Integer | 业务状态码，2000表示成功 | 2000 |
| msg | String | 响应消息描述 | 成功 |
| data | Array |  | — |
| anaAdd | Object |  | — |
| addCollectedCunt | String | 统计周期内新增收藏数 | 5w+ |
| addCommentCount | String | 统计周期内新增评论数 | 3668 |
| addInteractiveount | String | 统计周期内新增互动总量 | 42w+ |
| addLikeCount | String | 统计周期内新增点赞数 | 36w+ |
| addShareCount | String | 统计周期内新增分享数 | 8331 |
| collectedCount | String | 累计收藏总数 | 6w+ |
| interactiveCount | String | 累计互动总量 | 43w+ |
| useCommentCount | String | 累计评论总数 | 3686 |
| useLikeCount | String | 累计点赞总数 | 36w+ |
| useShareCount | String | 累计分享总数 | 8347 |
| coverUrl | String | 笔记封面图URL | https://sns-img-hw.xhscdn.net/1040g2sg323enli0u70l05pg92v6ipjjs5dsfcvg?imageView2/2/w/1080/format/webp |
| desc | String | 笔记正文描述 | 太真实了 |
| fans | String | 作者粉丝数 | 5w+ |
| photoJumpUrl | String | 笔记详情页链接 | https://www.xiaohongshu.com/explore/6a71b5c4000000002102197d |
| publicTime | String | 笔记发布时间 | 2026-08-04 17:49:56 |
| title | String | 笔记标题 | 男生吃到好吃的东西时 |
| userHeadUrl | String | 作者头像URL | https://sns-avatar-qc.xhscdn.com/avatar/1040g2jo31lcjl1vh4u005pg92v6ipjjs3opnghg?imageView2/2/w/120/format/jpg |
| userJumpUrl | String | 作者个人主页链接 | https://www.xiaohongshu.com/user/profile/660917cd000000000b00ce7c |
| userName | String | 作者昵称 | 老王. |

---

## 请求示例

```bash

```

---

## 响应示例

```json
{
  "code": 2000,
  "data": [
    {
      "anaAdd": {
        "addCollectedCunt": "5w+",
        "addCommentCount": "3668",
        "addInteractiveount": "42w+",
        "addLikeCount": "36w+",
        "addShareCount": "8331",
        "collectedCount": "6w+",
        "interactiveCount": "43w+",
        "useCommentCount": "3686",
        "useLikeCount": "36w+",
        "useShareCount": "8347"
      },
      "coverUrl": "https://sns-img-hw.xhscdn.net/1040g2sg323enli0u70l05pg92v6ipjjs5dsfcvg?imageView2/2/w/1080/format/webp",
      "desc": "太真实了",
      "fans": "5w+",
      "photoJumpUrl": "https://www.xiaohongshu.com/explore/6a71b5c4000000002102197d",
      "publicTime": "2026-08-04 17:49:56",
      "title": "男生吃到好吃的东西时",
      "userHeadUrl": "https://sns-avatar-qc.xhscdn.com/avatar/1040g2jo31lcjl1vh4u005pg92v6ipjjs3opnghg?imageView2/2/w/120/format/jpg",
      "userJumpUrl": "https://www.xiaohongshu.com/user/profile/660917cd000000000b00ce7c",
      "userName": "老王."
    },
    {
      "anaAdd": {
        "addCollectedCunt": "2w+",
        "addCommentCount": "1w+",
        "addInteractiveount": "38w+",
        "addLikeCount": "34w+",
        "addShareCount": "9w+",
        "collectedCount": "2w+",
        "interactiveCount": "39w+",
        "useCommentCount": "1w+",
        "useLikeCount": "35w+",
        "useShareCount": "9w+"
      },
      "coverUrl": "https://sns-img-bd.xhscdn.com/1040g008323enonfcgba04a62ggacosol8cjb9q0?imageView2/2/w/360/format/jpg/q/75",
      "desc": "#抽象 #精神状态belike #迷惑行为大赏",
      "fans": "39w+",
      "photoJumpUrl": "https://www.xiaohongshu.com/explore/6a71b6a2000000002800b71f",
      "publicTime": "2026-08-04 17:53:38",
      "title": "偷偷把老爸P到他正看的电影里看看他的反应",
      "userHeadUrl": "https://sns-avatar-qc.xhscdn.com/avatar/621b866e26891684e58f4993.jpg?imageView2/2/w/120/format/jpg",
      "userJumpUrl": "https://www.xiaohongshu.com/user/profile/5b1a94cc11be1018354d7315",
      "userName": "段庆玺姓段"
    }
  ],
  "msg": "成功"
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
