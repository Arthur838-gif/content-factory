"""P9 公众号链路测试：RedFox 接口解析 / 采样器 / 判定阈值边界 / 入库分流 /
队列 collector=gzh_sample / 手动喂样本 API / WechatArticle 校验器 / 标题打分
平台分流 / 回填与效果分扩展 / PIL 封面登记与素材包。

全部打桩（假 redfox._post 或 monkeypatch），零真实调用、零计费、不联网。
"""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import config
from app.adapters import wechat as wechat_adapter
from app.collectors import base as collectors_base
from app.collectors import gzh_sample, redfox
from app.db import session_scope
from app.models import Article, Asset, HotItem, PublishRecord, Topic, ViralSample
from app.schemas import HotItem as HotItemSchema
from app.schemas import ManualGzhSampleInput, WechatArticle
from app.services import radar, sampling_jobs, scoring, titles
from app.services.worker import SamplingWorker


def _gzh_item(**overrides) -> HotItemSchema:
    """构造一条公众号 HotItem：raw.article 承载 reads/watches/shares。"""
    article = {
        "title": "AI 编程实战",
        "workUrl": "https://mp.weixin.qq.com/s/abc",
        "readCount": 20000,
        "watchCount": 100,
        "shareCount": 60,
        "likeCount": 300,
        "collectCount": 50,
        "commentCount": 20,
    }
    article.update(overrides.pop("article", {}))
    base = dict(
        source="gzh",
        title=article["title"],
        url=article["workUrl"],
        author="测试号",
        fans=0,
        likes=article["likeCount"],
        collects=article["collectCount"],
        comments=article["commentCount"],
        raw={"article": article},
    )
    base.update(overrides)
    return HotItemSchema(**base)


# ---- RedFox 接口层 ----

def test_gzh_search_articles_payload_and_parse(monkeypatch):
    calls: list[tuple] = []

    def fake_post(path, payload, extra_headers=None):
        calls.append((path, dict(payload)))
        return {
            "code": 2000,
            "data": {
                "list": [
                    {"title": "AI 文章", "workUrl": "https://mp.weixin.qq.com/s/1",
                     "readCount": 100, "likeCount": "1,200", "collectCount": 30,
                     "commentCount": 5, "content": "正文"},
                    "不是字典",  # 非字典条目丢弃
                ],
                "total": 2,
            },
        }

    monkeypatch.setattr(redfox, "_post", fake_post)
    rows = redfox.gzh_search_articles("AI工具", offset=20)

    assert calls == [(
        redfox.GZH_SEARCH_ARTICLE_PATH,
        {"keyword": "AI工具", "offset": 20, "sortType": "_4"},
    )]
    assert len(rows) == 1 and rows[0]["title"] == "AI 文章"

    items = redfox.parse_gzh_articles(rows)
    assert len(items) == 1
    it = items[0]
    # 字段映射：likes/collects/comments 走 _to_int 容错（"1,200" → 1200），fans 恒 0
    assert (it.source, it.fans, it.likes, it.collects, it.comments) == ("gzh", 0, 1200, 30, 5)
    assert it.url == "https://mp.weixin.qq.com/s/1"
    assert it.raw["article"]["readCount"] == 100  # 全量指标留在 raw.article


def test_gzh_search_articles_missing_list_raises(monkeypatch):
    monkeypatch.setattr(redfox, "_post", lambda path, payload, extra_headers=None: {"code": 2000, "data": {"total": 0}})
    with pytest.raises(redfox.RedFoxError, match="list"):
        redfox.gzh_search_articles("AI工具")


def test_parse_gzh_articles_skips_incomplete():
    rows = [
        {"workUrl": "https://mp.weixin.qq.com/s/1"},  # 无标题 → 跳过
        {"title": "无链接文章"},  # 无 workUrl → 跳过
        {"title": "正常", "workUrl": "https://mp.weixin.qq.com/s/3"},
    ]
    items = redfox.parse_gzh_articles(rows)
    assert len(items) == 1 and items[0].title == "正常"


def test_gzh_article_detail(monkeypatch):
    def fake_post(path, payload, extra_headers=None):
        assert path == redfox.GZH_ARTICLE_DETAIL_PATH
        assert payload == {"url": "https://mp.weixin.qq.com/s/abc"}
        return {"code": 2000, "data": {"title": "详情", "readCount": 999}}

    monkeypatch.setattr(redfox, "_post", fake_post)
    assert redfox.gzh_article_detail("https://mp.weixin.qq.com/s/abc")["readCount"] == 999

    monkeypatch.setattr(redfox, "_post", lambda path, payload, extra_headers=None: {"code": 2000, "data": ["列表"]})
    with pytest.raises(redfox.RedFoxError):
        redfox.gzh_article_detail("https://mp.weixin.qq.com/s/abc")


def test_sensitive_word_search_platform_param(monkeypatch):
    monkeypatch.setattr(config, "REDFOX_API_KEY", "ak_test")
    seen: list[dict] = []

    def fake_post(path, payload, extra_headers=None):
        seen.append(dict(payload))
        assert extra_headers and extra_headers.get("X-API-KEY") == "ak_test"
        return {"code": 2000, "data": {"content": "干净", "prohibitedWordsType": []}}

    monkeypatch.setattr(redfox, "_post", fake_post)
    redfox.sensitive_word_search("内容", platform="微信公众号")
    redfox.sensitive_word_search("内容")  # 默认仍小红书
    assert seen[0]["platform"] == "微信公众号"
    assert seen[0]["source"] == "微信公众号违禁词查询-GitHub"
    assert seen[1]["platform"] == "小红书"


# ---- 采样器 ----

def test_gzh_collector_queries_priority(seeded_env, monkeypatch):
    monkeypatch.setattr(config, "GZH_SAMPLE_KEYWORDS", ["环境词"])
    collector = gzh_sample.GzhSampleCollector()
    assert collector._queries() == ["环境词"]
    assert gzh_sample.GzhSampleCollector(keywords=["显式词"])._queries() == ["显式词"]
    # 无环境词、无栏目 → 领域词表兜底（fixture 词表非空）
    monkeypatch.setattr(config, "GZH_SAMPLE_KEYWORDS", [])
    assert collector._queries()
    # 上限封顶
    monkeypatch.setattr(config, "GZH_SAMPLE_MAX_QUERIES", 2)
    assert len(gzh_sample.GzhSampleCollector(keywords=["a", "b", "c", "d"])._queries()) == 2


def test_gzh_collector_no_key_raises(monkeypatch):
    monkeypatch.setattr(config, "REDFOX_API_KEY", "")
    with pytest.raises(redfox.RedFoxError, match="未配置"):
        gzh_sample.GzhSampleCollector().fetch_keyword("AI工具")


def test_gzh_collector_fetch_aggregates_and_tags_keyword(monkeypatch):
    items = [_gzh_item(), _gzh_item(article={"workUrl": "https://mp.weixin.qq.com/s/2"})]
    monkeypatch.setattr(gzh_sample, "search_gzh_items", lambda kw: items)
    got = gzh_sample.GzhSampleCollector(keywords=["AI工具"]).fetch()
    assert len(got) == 2
    assert all(it.raw.get("keyword") == "AI工具" for it in got)  # 检索词写进 raw 供领域放行

    def boom(kw):
        raise redfox.RedFoxError("余额不足")

    monkeypatch.setattr(gzh_sample, "search_gzh_items", boom)
    with pytest.raises(redfox.RedFoxError):  # 失败不静默降级，由调用方计熔断
        gzh_sample.GzhSampleCollector(keywords=["AI工具"]).fetch()


# ---- 判定阈值 ----

def test_gzh_viral_score_formula():
    item = _gzh_item()
    # (likes + watches + 2×collects + 3×shares + 3×comments) ÷ reads
    # = (300 + 100 + 100 + 180 + 60) ÷ 20000 = 0.037
    assert radar.gzh_viral_score(item) == pytest.approx(0.037)


def test_is_gzh_viral_boundaries(monkeypatch):
    monkeypatch.setattr(config, "GZH_READS_MIN", 10000)
    monkeypatch.setattr(config, "GZH_SCORE_MIN", 0.08)
    # 阅读量差 1 不达标：互动密度再高也 False（reads 是指标有效性下限）
    assert radar.is_gzh_viral(_gzh_item(article={"readCount": 9999, "shareCount": 900000})) is False
    # 双双压线 → True（闭区间）
    edge = _gzh_item(article={"readCount": 10000, "likeCount": 0, "watchCount": 0,
                              "shareCount": 267, "collectCount": 0, "commentCount": 0})
    # (3×267) ÷ 10000 = 0.0801 ≥ 0.08
    assert radar.is_gzh_viral(edge) is True
    # 阅读达标但密度差一点 → False
    low = _gzh_item(article={"readCount": 10000, "shareCount": 260, "likeCount": 0,
                             "watchCount": 0, "collectCount": 0, "commentCount": 0})
    assert radar.is_gzh_viral(low) is False


def test_gzh_score_zero_reads_no_division_error():
    item = _gzh_item(article={"readCount": 0, "shareCount": 5, "likeCount": 0,
                              "watchCount": 0, "collectCount": 0, "commentCount": 0})
    assert radar.gzh_viral_score(item) == pytest.approx(15.0)  # 3×5÷1，分母按 1 计
    assert radar.is_gzh_viral(item) is False  # reads 0 < 下限，绝不入选


def test_gzh_metrics_string_fallback():
    item = _gzh_item(article={"readCount": "1.5万", "watchCount": "30", "shareCount": None})
    assert radar.gzh_reads(item) == 15000
    assert radar._gzh_metric(item.raw["article"], "watchCount") == 30
    assert radar._gzh_metric(item.raw["article"], "shareCount") == 0


# ---- 入库分流（gzh 绝不走 xhs 判定） ----

def test_persist_gzh_uses_gzh_pipeline(seeded_env, monkeypatch):
    """fans=0 的 gzh 条目若误入 xhs 判定，爆文率会除以 1 全数误判——
    这里把 process_xhs_item 设为炸弹，证明分流正确。"""
    def bomb(*args, **kwargs):
        raise AssertionError("gzh 条目绝不能走 process_xhs_item")

    monkeypatch.setattr(radar, "process_xhs_item", bomb)
    viral_item = _gzh_item(article={"readCount": 20000, "shareCount": 900, "likeCount": 200,
                                    "watchCount": 100, "collectCount": 100, "commentCount": 50})
    plain_item = _gzh_item(article={"workUrl": "https://mp.weixin.qq.com/s/plain", "readCount": 0})

    with session_scope() as session:
        result = collectors_base.persist_hot_items(session, [viral_item, plain_item], collector="gzh_sample")
        assert result.inserted == 2
        assert result.viral_created == 1  # 只有达标条目入选
        rows = session.query(HotItem).filter(HotItem.source == "gzh").all()
        assert len(rows) == 2
        samples = session.query(ViralSample).all()
        assert len(samples) == 1
        # evidence 快照带公众号口径指标
        topic = session.query(Topic).first()
        metrics = topic.evidence["items"][0]["metrics"]
        assert metrics["reads"] == 20000
        assert metrics["watches"] == 100
        assert metrics["shares"] == 900


def test_persist_gzh_auto_skips_judgment_when_reads_missing(seeded_env):
    """auto 且 readCount 缺失 → 只落 hot_items 不判定（与 xhs fans 缺失同语义）。"""
    item = _gzh_item(article={"readCount": 0, "likeCount": 99999})
    with session_scope() as session:
        result = collectors_base.persist_hot_items(session, [item], collector="gzh_sample")
        assert result.inserted == 1 and result.viral_created == 0
        assert session.query(ViralSample).count() == 0


# ---- 队列 collector=gzh_sample ----

def test_enqueue_and_worker_run_gzh_job(seeded_env, monkeypatch):
    job, created = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"], collector="gzh_sample")
    assert created and job.collector == "gzh_sample" and job.dedupe_key == "manual:gzh_sample"
    # 同键活跃去重（防连点重复计费）
    again, created2 = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"], collector="gzh_sample")
    assert created2 is False and again.id == job.id

    calls: list[str] = []

    def fake_fetch(self, keyword):
        calls.append(keyword)
        return [_gzh_item(article={"readCount": 20000, "shareCount": 900})], "redfox"

    monkeypatch.setattr(gzh_sample.GzhSampleCollector, "fetch_keyword", fake_fetch)
    assert SamplingWorker().run_once() == "succeeded"
    assert calls == ["AI工具"]
    row = sampling_jobs.get_job(job.id)
    assert row.fetched == 1 and row.inserted == 1 and row.viral_created == 1
    assert row.meta["sources"] == {"AI工具": "redfox"}
    with session_scope() as session:
        assert session.query(HotItem).filter(HotItem.source == "gzh").count() == 1


def test_worker_rejects_unknown_collector(isolated_env):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["x"], collector="nope_sample")
    assert SamplingWorker().run_once() == "failed"
    row = sampling_jobs.get_job(job.id)
    assert row.error_type == "unsupported_collector"


def test_gzh_sampling_api_contract(isolated_env, monkeypatch):
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/sampling/jobs", json={"collector": "gzh_sample", "keywords": ["AI工具"]})
    assert r.status_code == 202 and r.json()["created"] is True
    r = client.post("/api/sampling/jobs", json={"collector": "hotboard"})
    assert r.status_code == 422  # 白名单校验


def test_gzh_default_keywords_resolution(isolated_env, monkeypatch):
    monkeypatch.setattr(config, "GZH_SAMPLE_KEYWORDS", ["词A", "词B"])
    job, created = sampling_jobs.enqueue(kind="scheduled", collector="gzh_sample")
    assert created and job.keywords == ["词A", "词B"]


# ---- 栏目联动：定向采样 + 素材池（P9 后补）----

def test_pillar_gzh_sampling_endpoint(isolated_env):
    """栏目定向采样支持公众号采集器：独立去重键与小红书并行不挤占。"""
    from app.main import app
    from app.models import Pillar

    with session_scope() as session:
        pillar = Pillar(name="AI 工具周报", domain="AI与编程", slots_per_week=1,
                        keywords=["AI工具", "AIGC"], active=True)
        session.add(pillar)
        session.flush()
        pid = pillar.id
    client = TestClient(app)
    r = client.post(f"/api/pillars/{pid}/sample?collector=gzh_sample")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["created"] is True
    assert body["job"]["collector"] == "gzh_sample" and body["job"]["kind"] == "pillar"
    assert body["job"]["keywords"] == ["AI工具", "AIGC"] and body["job"]["pillar_id"] == pid
    # 去重键按采集器分开：小红书任务与公众号任务可并存
    rx = client.post(f"/api/pillars/{pid}/sample?collector=xhs_sample")
    assert rx.status_code == 202 and rx.json()["created"] is True
    # 同采集器连点 → 复用在跑任务不重复计费
    r2 = client.post(f"/api/pillars/{pid}/sample?collector=gzh_sample")
    assert r2.status_code == 202 and r2.json()["created"] is False
    assert r2.json()["job"]["id"] == body["job"]["id"]
    # 白名单外采集器 422
    assert client.post(f"/api/pillars/{pid}/sample?collector=hotboard").status_code == 422


def test_matched_pool_includes_gzh(seeded_env):
    """素材池三源同吃：gzh 文章参与栏目周排期与主题规划（raw.keyword 归队）。"""
    from app.models import Pillar
    from app.services import pillar as pillar_service

    with session_scope() as session:
        pillar = Pillar(name="AI 工具周报", domain="AI与编程", slots_per_week=1,
                        keywords=["AI工具"], active=True)
        session.add(pillar)
        # 标题不含关键词，但 raw.keyword 记录了采样词 → 靠采样词归队
        gzh_row = HotItem(source="gzh", title="五个提升效率的智能助手",
                          url="https://mp.weixin.qq.com/s/pool1", author="号", fans=0,
                          likes=500, collects=10, comments=5,
                          raw={"article": {"readCount": 30000}, "keyword": "AI工具"})
        session.add(gzh_row)
        session.flush()
        week_start, _ = pillar_service.week_bounds()
        matched = pillar_service._matched_pool(session, pillar, week_start)
        assert [(i.id, kw) for i, kw in matched] == [(gzh_row.id, "AI工具")]


# ---- 手动喂公众号样本（URL 抓取式） ----

_DETAIL = {
    "title": "AI 编程的十个技巧",
    "workUrl": "https://mp.weixin.qq.com/s/manual1",
    "readCount": 30000, "watchCount": 200, "shareCount": 700,
    "likeCount": 400, "collectCount": 100, "commentCount": 80,
    "summary": "一篇讲 AI 编程的干货", "content": "正文全文",
}


def _gzh_manual_client(monkeypatch, detail=None, fail=None):
    monkeypatch.setattr(config, "REDFOX_API_KEY", "ak_test")
    calls: list[str] = []

    def fake_detail(url):
        calls.append(url)
        if fail is not None:
            raise redfox.RedFoxError(fail)
        return dict(detail if detail is not None else _DETAIL)

    monkeypatch.setattr(redfox, "gzh_article_detail", fake_detail)
    from app.main import app
    return TestClient(app), calls


def test_gzh_manual_happy_path(isolated_env, monkeypatch):
    client, calls = _gzh_manual_client(monkeypatch)
    r = client.post("/api/viral-samples/gzh-manual", json={
        "url": "https://mp.weixin.qq.com/s/manual1", "domain": "AI与编程"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert calls == ["https://mp.weixin.qq.com/s/manual1"]  # 恰好 1 次计费调用
    assert data["viral"] is True  # (400+200+200+2100+240)/30000 = 0.1047 ≥ 0.08
    assert data["topic_id"] is not None
    with session_scope() as session:
        row = session.get(HotItem, data["hot_item_id"])
        assert row.source == "gzh" and row.fans == 0
        assert row.raw["entry"] == "manual_url"
        assert row.raw["article"]["readCount"] == 30000


def test_gzh_manual_dup_409_no_billing(isolated_env, monkeypatch):
    with session_scope() as session:
        session.add(HotItem(source="gzh", title="已入库", url="https://mp.weixin.qq.com/s/manual1",
                            raw={"article": {}}))
    client, calls = _gzh_manual_client(monkeypatch)
    r = client.post("/api/viral-samples/gzh-manual", json={
        "url": "https://mp.weixin.qq.com/s/manual1", "domain": "AI与编程"})
    assert r.status_code == 409
    assert calls == []  # 计费调用前先查重，不白花


def test_gzh_manual_redfox_error_502(isolated_env, monkeypatch):
    client, _ = _gzh_manual_client(monkeypatch, fail="HTTP 401：鉴权失败")
    r = client.post("/api/viral-samples/gzh-manual", json={
        "url": "https://mp.weixin.qq.com/s/boom", "domain": "AI与编程"})
    assert r.status_code == 502
    with session_scope() as session:
        assert session.query(HotItem).count() == 0  # 不写半成品


def test_gzh_manual_detail_without_title_422(isolated_env, monkeypatch):
    client, _ = _gzh_manual_client(monkeypatch, detail={"readCount": 100})
    r = client.post("/api/viral-samples/gzh-manual", json={
        "url": "https://mp.weixin.qq.com/s/notitle", "domain": "AI与编程"})
    assert r.status_code == 422


def test_gzh_manual_input_schema():
    data = ManualGzhSampleInput(url="https://mp.weixin.qq.com/s/x", domain="AI与编程")
    assert data.domain == "AI与编程"
    with pytest.raises(ValidationError):
        ManualGzhSampleInput(url="ftp://不是http", domain="x")
    with pytest.raises(ValidationError):
        ManualGzhSampleInput(url="https://ok", domain="")


# ---- WechatArticle 平台校验器 ----

def test_wechat_article_validators():
    WechatArticle(title="三" * 30, digest="摘" * 54, content_md="文" * 3000)  # 压线通过
    with pytest.raises(ValidationError, match="30 字"):
        WechatArticle(title="超" * 31, digest="x", content_md="y")
    with pytest.raises(ValidationError, match="54 字"):
        WechatArticle(title="t", digest="超" * 55, content_md="y")
    with pytest.raises(ValidationError, match="3000 字"):
        WechatArticle(title="t", digest="d", content_md="超" * 3001)


# ---- 标题打分平台分流 ----

def test_titles_score_platform_split(isolated_env):
    xhs = titles.score("AI 工具测评", platform="xhs")
    wechat = titles.score("AI 工具测评", platform="wechat")
    assert xhs["total"] <= 10 and len(xhs["dimensions"]) == 6  # 六维 0-10 制
    assert wechat["total"] <= 100 and len(wechat["dimensions"]) == 4  # 四维 0-100 制
    assert wechat["grade"] in ("S", "A", "B", "C")
    with pytest.raises(ValueError):
        titles.score("标题", platform="bilibili")
    with pytest.raises(ValueError):
        titles.score("   ", platform="wechat")


def test_titles_score_api_platform(isolated_env):
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/titles/score", json={"title": "AI 工具测评", "platform": "wechat"})
    assert r.status_code == 200 and r.json()["total"] <= 100
    r = client.post("/api/titles/score", json={"title": "AI 工具测评"})
    assert r.status_code == 200 and r.json()["total"] <= 10  # 默认 xhs
    r = client.post("/api/titles/score", json={"title": "标题", "platform": "bilibili"})
    assert r.status_code == 422


# ---- 回填与效果分扩展 ----

def test_engagement_extension(monkeypatch):
    for name in ("SCORE_W_LIKES", "SCORE_W_COLLECTS", "SCORE_W_COMMENTS", "SCORE_W_WATCHES", "SCORE_W_SHARES"):
        monkeypatch.setattr(config, name, 1.0)
    # watches/shares 各按权重 1 计入；xhs 旧记录缺这两键 → 不受影响
    assert scoring.engagement({"likes": 1, "collects": 1, "comments": 1}) == 3.0
    assert scoring.engagement({"likes": 1, "collects": 1, "comments": 1, "watches": 1, "shares": 1}) == 5.0
    assert scoring.engagement(None) == 0.0


def test_publish_metrics_schema():
    from app.api.routes_articles import PublishMetrics
    m = PublishMetrics(reads=100, watches=10, shares=5, likes=1, collects=2, comments=3)
    assert m.model_dump()["reads"] == 100
    with pytest.raises(ValidationError):
        PublishMetrics(reads=-1)
    with pytest.raises(ValidationError):
        PublishMetrics(shares=10**9 + 1)


def _seed_wechat_article() -> int:
    with session_scope() as session:
        topic = Topic(title="测试选题", domain="AI与编程", source="manual")
        session.add(topic)
        session.flush()
        article = Article(
            topic_id=topic.id, prompt_id=None, platform="wechat", title="测试公众号文章",
            content="正文内容", tags=[], meta={"digest": "这是摘要"},
            status="ready",
        )
        session.add(article)
        session.flush()
        return article.id


def test_publish_wechat_with_new_metrics(isolated_env):
    article_id = _seed_wechat_article()
    from app.main import app
    client = TestClient(app)
    r = client.post(f"/api/articles/{article_id}/publish", json={
        "platform": "wechat", "account": "测试号",
        "metrics": {"reads": 10000, "watches": 200, "shares": 150, "likes": 300, "collects": 50, "comments": 20},
    })
    assert r.status_code == 201, r.text
    with session_scope() as session:
        record = session.query(PublishRecord).filter(PublishRecord.article_id == article_id).one()
        assert record.metrics["reads"] == 10000
        assert record.metrics["watches"] == 200
        assert record.metrics["shares"] == 150


# ---- PIL 封面登记与素材包 ----

def test_render_cover_asset_and_package(isolated_env):
    article_id = _seed_wechat_article()
    with session_scope() as session:
        assert wechat_adapter.render_cover_asset(session, article_id, cover_text="测试封面文案") == 1
        rows = session.query(Asset).filter(Asset.article_id == article_id).all()
        assert len(rows) == 1
        cover = rows[0]
        assert cover.kind == "cover" and (cover.width, cover.height) == (900, 383)
        assert cover.path == f"assets/{article_id}/01_cover.png"
        assert (config.ASSETS_DIR / str(article_id) / "01_cover.png").is_file()
        # 幂等：重复渲染清旧文件 + 重建行，不叠 assets
        wechat_adapter.render_cover_asset(session, article_id, cover_text="换一版封面")
        assert session.query(Asset).filter(Asset.article_id == article_id).count() == 1

    from app.main import app
    client = TestClient(app)
    r = client.get(f"/api/articles/{article_id}/package")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = archive.namelist()
        assert "title.txt" in names and "content.txt" in names
        assert "digest.txt" in names  # 公众号独有：发布后台摘要位
        assert any(n.startswith("images/01_cover") for n in names)
        assert archive.read("digest.txt").decode("utf-8") == "这是摘要"


def test_package_requires_assets(isolated_env):
    article_id = _seed_wechat_article()  # ready 但无素材
    from app.main import app
    client = TestClient(app)
    assert client.get(f"/api/articles/{article_id}/package").status_code == 409


def test_render_cover_imaging_failure_marks_article(isolated_env, monkeypatch):
    """封面渲染失败在落库事务内抛 ImagingError（由调用方转 article failed）。"""
    from app.services import imaging
    monkeypatch.setattr(imaging, "render_wechat_cover",
                        lambda text, out: (_ for _ in ()).throw(imaging.ImagingError("字体缺失")))
    article_id = _seed_wechat_article()
    with pytest.raises(imaging.ImagingError):
        with session_scope() as session:
            wechat_adapter.render_cover_asset(session, article_id, cover_text="x")
