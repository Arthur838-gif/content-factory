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
