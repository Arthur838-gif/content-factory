# 小红书爆款笔记洞察

小红书爆款笔记洞察

**`POST`** `https://redfox.hk/story/api/xhs/search/search`

---

## API 说明

**Method**: `POST`
**Host**: `https://redfox.hk`
**Path**: `/story/api/xhs/search/search`

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
| keyword | String | 否 | 搜索关键词（可选，不传则按互动数降序返回最热门数据） | AIGC创业 |
| pageNum | Integer | 否 | 页码，从1开始（可选，默认1，无关键词时生效） | 1 |
| pageSize | Integer | 否 | 每页条数（可选，默认10，最大50，无关键词时生效） | 10 |
| startDate | String | 否 | 时间范围-开始日期，格式：yyyy-MM-dd（可选） | 2026-07-01 |
| endDate | String | 否 | 时间范围-结束日期，格式：yyyy-MM-dd（可选） | 2026-07-31 |

---

## 返回值与结构

统一包装一般为 `code`、`message`/`msg`、`data`（以实际服务为准）。

---

## 响应字段

| 字段 | 类型 | 说明 | 示例 |
| --- | --- | --- | --- |
| articles | Array | 搜索结果列表 | — |
| authorFans | Integer | 作者粉丝数 | 8614 |
| authorId | String | 作者ID | 66b81a14000000001d020e53 |
| authorNickname | String | 作者昵称 | 飞飞在学AI |
| collectedCount | Integer | 收藏数 | 1120 |
| commentsCount | Integer | 评论数 | 26 |
| cover | String | 笔记封面图 | http://sns-img-qc.xhscdn.com/spectrum/1040g0k0322bk53e472005plo38a7c3ija46k838?imageView2/2/w/360/format/jpg/q/75 |
| createTime | String | 发布时间 | 2026-07-08 13:05:29 |
| desc | String | 笔记正文（desc） | 不要再做demo了！搭建属于自己的Agent产品吧！EdgeOne Makers 一键托管 AI 智能体，深度研究模板开箱即用，代码同步云端24小时稳定服务，从原型直接落地成品！#AI智能体  #AIGC创业  #AI工具分享  #腾讯云  #EdgeOne  #AIAgent  #EdgeOneMakers #howto玩坏AI #howto用AI抢救一切 #howto实现一万种vibecoding |
| id | String | 笔记ID | 6a4dcb110000000016025b73 |
| interactiveCount | Integer | 总互动数 | 1775 |
| likedCount | Integer | 点赞数 | 629 |
| popularityScore | Double | 热度得分（0-3分） | 1 |
| recencyScore | Double | 时效得分（0-2分） | 0.5 |
| relevanceScore | Double | 相关性得分（0-10分） | 6 |
| sharedCount | Integer | 分享数 | 228 |
| shareInfoLink | String | 笔记链接 | https://www.xiaohongshu.com/explore/6a4dcb110000000016025b73 |
| title | String | 笔记标题 | 从0到上线：用Fable 5搭一个投研Agent |
| topicsName | String | 话题标签（用于标签匹配和推荐） | null |
| totalScore | Double | 综合得分（三因子累加） | 7.5 |
| hotTopics | Array | 热门话题推荐（基于话题聚合TOP10） | — |
| articleCount | Integer | 该话题下的作品数量 | 31032 |
| topic | String | 话题名称 | 好视频扶持计划 |
| totalInteractiveCount | Integer | 该话题下的总互动数 | 296775935 |
| keyword | String | 搜索关键词 | AIGC创业 |
| latestHotArticles | Array | 最新爆文兜底（搜索结果不足时推荐） | — |
| pageNum | Integer | 当前页码 | 1 |
| pageSize | Integer | 每页条数 | 50 |
| relatedSearches | Array | 相关搜索推荐（基于话题/关键词共现） | — |
| tips | String | 结果层级提示 ≥10条: null（正常展示） 3-9条: "仅找到 X 条结果，以下内容可能也对你有帮助" 1-2条: "相关结果较少" 0条: "未找到相关结果，为你推荐以下热门内容" | 仅找到 3 条结果，以下内容可能也对你有帮助 |
| total | Integer | 总命中数 | 3 |

---

## 请求示例

```bash
请求参数：
{
  "keyword": "AIGC创业",
  "pageNum": 1,
  "pageSize": 10,
  "startDate": "2026-07-01",
  "endDate": "2026-07-31"
}
```

---

## 响应示例

```json
{
  "articles": [
    {
      "authorFans": 8614,
      "authorId": "66b81a14000000001d020e53",
      "authorNickname": "飞飞在学AI",
      "collectedCount": 1120,
      "commentsCount": 26,
      "cover": "http://sns-img-qc.xhscdn.com/spectrum/1040g0k0322bk53e472005plo38a7c3ija46k838?imageView2/2/w/360/format/jpg/q/75",
      "createTime": "2026-07-08 13:05:29",
      "desc": "不要再做demo了！搭建属于自己的Agent产品吧！\nEdgeOne Makers 一键托管 AI 智能体，深度研究模板开箱即用，代码同步云端24小时稳定服务，从原型直接落地成品！#AI智能体  #AIGC创业  #AI工具分享  #腾讯云  #EdgeOne  #AIAgent  #EdgeOneMakers #howto玩坏AI #howto用AI抢救一切 #howto实现一万种vibecoding",
      "id": "6a4dcb110000000016025b73",
      "interactiveCount": 1775,
      "likedCount": 629,
      "popularityScore": 1,
      "recencyScore": 0.5,
      "relevanceScore": 6,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a4dcb110000000016025b73",
      "sharedCount": 228,
      "title": "从0到上线：用Fable 5搭一个投研Agent",
      "topicsName": null,
      "totalScore": 7.5
    }
  ],
  "hotTopics": [
    {
      "articleCount": 31032,
      "topic": "好视频扶持计划",
      "totalInteractiveCount": 296775935
    },
    {
      "articleCount": 16326,
      "topic": "搞笑",
      "totalInteractiveCount": 288477814
    },
    {
      "articleCount": 8189,
      "topic": "万万没想到",
      "totalInteractiveCount": 198785601
    },
    {
      "articleCount": 8733,
      "topic": "抽象",
      "totalInteractiveCount": 137967129
    },
    {
      "articleCount": 12904,
      "topic": "世界杯聊个球",
      "totalInteractiveCount": 126248361
    },
    {
      "articleCount": 9457,
      "topic": "cos",
      "totalInteractiveCount": 109037326
    },
    {
      "articleCount": 4852,
      "topic": "内容过于真实",
      "totalInteractiveCount": 91146915
    },
    {
      "articleCount": 8193,
      "topic": "蛋仔派对",
      "totalInteractiveCount": 70920470
    },
    {
      "articleCount": 12102,
      "topic": "vlog",
      "totalInteractiveCount": 65191672
    },
    {
      "articleCount": 2731,
      "topic": "剧情",
      "totalInteractiveCount": 49063260
    }
  ],
  "keyword": "AIGC创业",
  "latestHotArticles": [
    {
      "authorFans": 288,
      "authorId": "5f06eabe0000000001001235",
      "authorNickname": "broisnotacat",
      "collectedCount": 77357,
      "commentsCount": 29290,
      "cover": "http://sns-img-hw.xhscdn.com/1040g008323iemph4nu005no6tav084hlfor9cmo?imageView2/2/w/360/format/jpg/q/75",
      "createTime": "2026-08-07 15:16:47",
      "desc": "#支持这个地球猫舔转论 #谢谢小猫 #让我们一起谢谢小猫 #地球没有小猫就转不了 #好视频扶持计划 #猫meme #没有小猫这个地球怎么转 #fyp #地球猫舔论 #小红书土猫大赏\n—————————\n请不要盗用，商用，以及二创我的视频，不然会找你麻烦噢：）",
      "id": "6a75865e0000000006006baa",
      "interactiveCount": 995754,
      "likedCount": 889107,
      "popularityScore": null,
      "recencyScore": null,
      "relevanceScore": null,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a75865e0000000006006baa",
      "sharedCount": 230074,
      "title": "让我们一起谢谢小猫！",
      "topicsName": null,
      "totalScore": null
    },
    {
      "authorFans": 319414,
      "authorId": "67b999f7000000000a03ca4e",
      "authorNickname": "富士小山",
      "collectedCount": 99929,
      "commentsCount": 3360,
      "cover": "https://sns-na-i2.xhscdn.com/1040g008323hc00fvnk5g5ptpj7riniie9c6gslo?imageView2/2/w/608/format/heif/q/56|imageMogr2/strip&redImage/frame/0/enhance/4&ap=22&sc=LF_PRV&sign=b976f7418b8eeb183debba56f3e8cc08&t=6a7c69bf&origin=0",
      "createTime": "2026-08-06 18:59:50",
      "desc": "超级简单，家里的T恤也可以这样改起来啦～\n#每天一个穿搭灵感 #同一件衣服穿出不一样感觉 #爆改帮帮忙 #爆改 #T恤改造#缝纫就有好心情 #超会穿企划 #本命穿搭@手工薯#remaker的夏天 #手工的夏天",
      "id": "6a746926000000003400f755",
      "interactiveCount": 727211,
      "likedCount": 623922,
      "popularityScore": null,
      "recencyScore": null,
      "relevanceScore": null,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a746926000000003400f755",
      "sharedCount": 13281,
      "title": "T恤的后面比前面好看怎么办？",
      "topicsName": null,
      "totalScore": null
    },
    {
      "authorFans": 202737,
      "authorId": "5e44316a00000000010028a3",
      "authorNickname": "小黄油",
      "collectedCount": 48496,
      "commentsCount": 3622,
      "cover": "http://sns-img-hw.xhscdn.net/1040g008323l366hhgae05ni465l08a53rnjt0bg?imageView2/2/w/1080/format/webp",
      "createTime": "2026-08-09 16:29:15",
      "desc": "#假装自己是探店博主 #出门在外身份是自己给的 #留学美国 #留学生 #探店 #加州 #外国人",
      "id": "6a783a5b000000002403e17d",
      "interactiveCount": 654342,
      "likedCount": 602224,
      "popularityScore": null,
      "recencyScore": null,
      "relevanceScore": null,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a783a5b000000002403e17d",
      "sharedCount": 3978,
      "title": "在美国假装探店博主，竟然收获了实心三明治！",
      "topicsName": null,
      "totalScore": null
    },
    {
      "authorFans": 41918,
      "authorId": "6a66f9470000000013020c00",
      "authorNickname": "爷奶的记录",
      "collectedCount": 35776,
      "commentsCount": 16564,
      "cover": "http://sns-img-bd.xhscdn.com/1040g008323hnv9esn2005qj6v53ks30062i9ab0?imageView2/2/w/360/format/jpg/q/75",
      "createTime": "2026-08-07 01:58:38",
      "desc": "#把镜头对准爷爷奶奶 #老一辈的爱 #老吾老以及人之老 #爷爷奶奶带孩子 #大自然是我的好朋友 #记录吧就现在 #老一辈的疼爱 #浪漫生活的记录者 #多陪陪老人 #想念爷爷奶奶",
      "id": "6a74cb4e0000000027021c73",
      "interactiveCount": 550624,
      "likedCount": 498284,
      "popularityScore": null,
      "recencyScore": null,
      "relevanceScore": null,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a74cb4e0000000027021c73",
      "sharedCount": 365,
      "title": "奶奶的日常",
      "topicsName": null,
      "totalScore": null
    },
    {
      "authorFans": 88062,
      "authorId": "5d0ca3a5000000001601eb7c",
      "authorNickname": "一禾的家居日记🏠",
      "collectedCount": 40649,
      "commentsCount": 10516,
      "cover": "http://sns-img-hw.xhscdn.net/1040g2sg323l57j6i74eg5n8ckeiljqrskel2bl0?imageView2/2/w/1080/format/webp",
      "createTime": "2026-08-09 17:35:30",
      "desc": "#有趣的蛋糕 #蛋糕的仪式感 #创意蛋糕 #晒蛋糕愿望会实现",
      "id": "6a7849e2000000002c007d59",
      "interactiveCount": 539141,
      "likedCount": 487976,
      "popularityScore": null,
      "recencyScore": null,
      "relevanceScore": null,
      "shareInfoLink": "https://www.xiaohongshu.com/explore/6a7849e2000000002c007d59",
      "sharedCount": 62618,
      "title": "真的太搞笑了…",
      "topicsName": null,
      "totalScore": null
    }
  ],
  "pageNum": 1,
  "pageSize": 50,
  "relatedSearches": [
    {
      "articleCount": 15248,
      "keyword": "AIGC"
    },
    {
      "articleCount": 12778,
      "keyword": "创业"
    },
    {
      "articleCount": 5324,
      "keyword": "aigc"
    },
    {
      "articleCount": 4741,
      "keyword": "AI工具"
    },
    {
      "articleCount": 4354,
      "keyword": "电商创业"
    },
    {
      "articleCount": 4349,
      "keyword": "ai"
    },
    {
      "articleCount": 4178,
      "keyword": "内容过于真实"
    },
    {
      "articleCount": 4042,
      "keyword": "女性成长"
    }
  ],
  "tips": "仅找到 3 条结果，以下内容可能也对你有帮助",
  "total": 3
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
