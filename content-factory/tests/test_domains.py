"""领域词表数据库化测试：种子导入 / upsert / 顺序匹配 / 停用 / API。"""
import pytest

from app.db import session_scope
from app.services import domain_service


def test_seed_imports_yaml_and_officials(seeded_env):
    stats = domain_service.seed_domains()  # isolated_env 已导一次，这里验证幂等
    assert stats["domains_created"] == 0 and stats["keywords_added"] == 0
    domains = domain_service.load_domains()
    # fixture 3 个自定义领域全部带词
    assert set(domains) >= {"AI与编程", "内容创作", "效率与副业"}
    assert domains["AI与编程"]  # 关键词非空
    # 官方 24 类目已登记（无词，不参与匹配词表但出现在领域列表）
    listing = domain_service.list_domains(include_disabled=True)
    names = [d["name"] for d in listing]
    assert "学习教育" in names and len(names) == 27  # 3 自定义 + 24 官方


def test_seed_custom_domains_precede_officials(seeded_env):
    listing = domain_service.list_domains()
    customs = [d for d in listing if d["type"] == "custom"]
    officials = [d for d in listing if d["type"] == "official"]
    assert all(c["ordering"] < o["ordering"] for c in customs for o in officials)


def test_upsert_domain_creates_and_merges(isolated_env):
    with session_scope() as s:
        created = domain_service.upsert_domain(s, "职场成长", ["汇报", "晋升"], source="user")
        assert created["created"] is True and created["keywords_total"] == 2
        # 再 upsert：只追加缺失词，已有词不动（重复写安全）
        merged = domain_service.upsert_domain(s, "职场成长", ["汇报", "跳槽"], source="user")
        assert merged["created"] is False and merged["added_keywords"] == ["跳槽"]
        assert merged["keywords_total"] == 3
    assert domain_service.load_domains()["职场成长"] == ["汇报", "晋升", "跳槽"]


def test_upsert_domain_validates_name(isolated_env):
    with pytest.raises(ValueError):
        with session_scope() as s:
            domain_service.upsert_domain(s, "x" * 65, ["词"])
    with pytest.raises(ValueError):
        with session_scope() as s:
            domain_service.upsert_domain(s, "   ", ["词"])
    # 全空白关键词不报错：领域照样建（官方类目就是无词领域）
    with session_scope() as s:
        result = domain_service.upsert_domain(s, "空白词领域", ["  "], source="user")
        assert result["created"] is True and result["keywords_total"] == 0


def test_upsert_official_appends_not_recreates(seeded_env):
    with session_scope() as s:
        result = domain_service.upsert_domain(s, "学习教育", ["考研"], source="user")
        assert result["created"] is False
    listing = domain_service.list_domains()
    official = next(d for d in listing if d["name"] == "学习教育")
    assert official["type"] == "official"


def test_match_domain_first_declared_wins(seeded_env):
    with session_scope() as s:
        # 追加一个晚声明的自定义领域，其词与 AI与编程 的词同时命中同一标题
        domain_service.upsert_domain(s, "后补领域", ["人工智能"], source="user")
    matched = domain_service.match_domain("人工智能新突破")
    assert matched is not None
    assert matched[0] == "AI与编程"  # fixture 先声明者优先
    assert domain_service.match_domain("完全不沾边的标题") is None


def test_disabled_domain_excluded_from_match(seeded_env):
    assert domain_service.set_domain_enabled("AI与编程", False) is True
    assert domain_service.set_domain_enabled("不存在的领域", True) is False
    domains = domain_service.load_domains()
    assert "AI与编程" not in domains
    # 停用领域仍可列出（含 enabled 标记），历史字符串快照不受影响
    listing = domain_service.list_domains(include_disabled=True)
    row = next(d for d in listing if d["name"] == "AI与编程")
    assert row["enabled"] is False
    assert domain_service.match_domain("人工智能新突破") is None


def test_domains_api(seeded_env):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    r = client.get("/api/domains")
    assert r.status_code == 200
    data = r.json()  # 直接返回数组（ordering 升序）
    assert any(d["name"] == "AI与编程" for d in data)

    r = client.post("/api/domains", json={"name": "API 领域", "keywords": ["接口"]})
    assert r.status_code == 201 and r.json()["created"] is True

    # 同名再建 = 合并（追加关键词），不是 409
    r = client.post("/api/domains", json={"name": "API 领域", "keywords": ["接口", "鉴权"]})
    assert r.status_code == 201 and r.json()["created"] is False
    assert r.json()["keywords_total"] == 2

    r = client.post("/api/domains", json={"name": "x" * 65, "keywords": ["词"]})
    assert r.status_code == 422

    r = client.post("/api/domains/AI与编程/keywords", json={"keywords": ["大模型"]})
    assert r.status_code == 201

    r = client.put("/api/domains/AI与编程/enabled", json={"enabled": False})
    assert r.status_code == 200
    r = client.put("/api/domains/不存在/enabled", json={"enabled": False})
    assert r.status_code == 404


def test_pillar_create_registers_domain_atomically(isolated_env):
    """建栏目：领域登记 + 关键词登记 + 栏目插入同一事务（P-2）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/pillars", json={
        "name": "测试栏目", "domain": "新领域甲", "slots_per_week": 1,
        "keywords": ["词一", "词二"], "active": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["domain"] == "新领域甲" and body["keywords"] == ["词一", "词二"]
    assert body["sampling_job_id"] is None  # PILLAR_AUTO_SAMPLE 已关
    # 同一事务里词表也登记好了
    with session_scope() as s:
        result = domain_service.upsert_domain(s, "新领域甲", [], source="user")
        assert result["created"] is False
    assert domain_service.load_domains()["新领域甲"] == ["词一", "词二"]


def test_keyword_domain_reverse_lookup(seeded_env):
    """采样词精确反查：命中返回 (领域, 词表原词)，先声明者优先；只精确匹配不做子串。"""
    with session_scope() as s:
        # 同一个词后补进新领域：反查仍取先声明的 AI与编程
        domain_service.upsert_domain(s, "后补领域", ["编程"], source="user")
    assert domain_service.keyword_domain("编程") == ("AI与编程", "编程")
    assert domain_service.keyword_domain("chatgpt") == ("AI与编程", "ChatGPT")  # 大小写不敏感，返回词表原词
    assert domain_service.keyword_domain("我在学编程") is None  # 不做子串匹配（那是 match_domain 的事）
    assert domain_service.keyword_domain("词表里没有的词") is None
    assert domain_service.keyword_domain("") is None
    # 预载词表口径（批量场景传 domains）与现查一致
    domains = domain_service.load_domains()
    assert domain_service.keyword_domain("存钱", domains) == ("效率与副业", "存钱")


def test_persist_keyword_fallback_admission(seeded_env):
    """关键词采样兜底入库：标题没命中词表时按采样词反查放行；热榜条目（无采样词）依旧过滤。"""
    from sqlalchemy import select

    from app.collectors.base import persist_hot_items
    from app.models import HotItem, Topic
    from app.schemas import HotItem as HotItemIn

    items = [
        # 采样召回：标题无词表词、采样词在词表 → 兜底放行，领域按采样词归属
        HotItemIn(source="weibo", title="我把6年微信读书记录做成了线上阅读角",
                  url="https://t.example/fallback", raw={"keyword": "编程"}),
        # 标题命中（写作→内容创作）优先于采样词（编程→AI与编程）
        HotItemIn(source="weibo", title="写作心法",
                  url="https://t.example/title-hit", raw={"keyword": "编程"}),
        # 热榜噪声：没有采样词可反查 → 维持标题过滤
        HotItemIn(source="weibo", title="周末去哪玩", url="https://t.example/hotboard"),
        # 采样词不在词表 → 依旧过滤
        HotItemIn(source="weibo", title="今天天气不错",
                  url="https://t.example/unknown-kw", raw={"keyword": "不存在的词"}),
    ]
    with session_scope() as s:
        result = persist_hot_items(s, items, collector="xhs_sample")
    assert (result.fetched, result.inserted, result.filtered_out) == (4, 2, 2)
    assert result.topics_created == 2

    urls = [i.url for i in items]
    with session_scope() as s:
        rows = {r.url: r for r in s.scalars(
            select(HotItem).where(HotItem.url.in_(urls))).all()}
        # 兜底与标题命中的入库，两条噪声没入库
        assert set(rows) == {"https://t.example/fallback", "https://t.example/title-hit"}
        # 采样词随行保留：pillar 排期按「标题或采样词」匹配素材
        assert rows["https://t.example/fallback"].raw["keyword"] == "编程"
        topics = {t.title: t for t in s.scalars(
            select(Topic).where(Topic.title.in_([i.title for i in items[:2]]))).all()}
    # 领域归属：兜底按采样词（AI与编程·编程），标题命中按标题（内容创作·写作）
    assert topics["我把6年微信读书记录做成了线上阅读角"].domain == "AI与编程"
    assert topics["写作心法"].domain == "内容创作"
