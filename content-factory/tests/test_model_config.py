"""模型配置测试：服务层（resolve / mock 矩阵 / 掩码）+ API 契约 + generator 接线。

关键不变量：
- resolve 解析链：DB active 行 > .env 回退，每次调用现查（切换即刻生效）；
- mock_enabled 矩阵兼容存量测试打桩（patch config.LLM_MOCK 即强制 mock）；
- api_key 明文绝不出现在任何 API 响应 / 页面（掩码 + 留空=保持）；
- usage.model 用实际调用的模型名、单价按该配置折算（多模型成本归因）。
"""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import session_scope
from app.models import ModelConfig
from app.services import model_config


@pytest.fixture
def env_llm(monkeypatch):
    """env 回退链的确定值（真实 .env 的值不可依赖，逐项钉死）。"""
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-env-key-123456")
    monkeypatch.setattr(config, "MODEL_NAME", "env-chat-model")
    monkeypatch.setattr(config, "GLM_IMAGE_MODEL", "env-image-model")
    monkeypatch.setattr(config, "LLM_PRICE_INPUT_PER_M", 0.3)
    monkeypatch.setattr(config, "LLM_PRICE_OUTPUT_PER_M", 0.5)
    monkeypatch.setattr(config, "LLM_MOCK", False)


@pytest.fixture
def client(isolated_env, env_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _create(session, purpose="text", name="配置", base_url="https://db.example/v1",
            api_key="sk-db-key-123456", model="db-chat-model", **kw):
    return model_config.create_config(
        session, purpose=purpose, name=name, base_url=base_url,
        api_key=api_key, model=model, **kw
    )


# ---- 服务层：resolve 回退链 ----

def test_resolve_env_fallback(isolated_env, env_llm):
    text = model_config.resolve("text")
    assert (text.source, text.name, text.model, text.base_url, text.api_key) == (
        "env", ".env 回退", "env-chat-model", "https://env.example/v1", "sk-env-key-123456"
    )
    image = model_config.resolve("image")
    assert (image.source, image.model) == ("env", "env-image-model")
    assert model_config.mock_enabled("text") is False
    assert model_config.mock_enabled("image") is False
    with pytest.raises(ValueError):
        model_config.resolve("video")


def test_resolve_active_row_and_mutex(isolated_env, env_llm):
    """active 行优先于 env；同用途切换互斥；图片用途不受文案切换影响。"""
    with session_scope() as session:
        first = _create(session, name="第一套")
        second = _create(session, name="第二套", model="glm-4.7", api_key="sk-second-999999")
        model_config.set_active(session, first)
        first_id, second_id = first.id, second.id
    assert model_config.resolve("text").model == "db-chat-model"

    with session_scope() as session:
        model_config.set_active(session, session.get(ModelConfig, second_id))
        assert session.get(ModelConfig, first_id).is_active is False  # 同事务互斥
    resolved = model_config.resolve("text")
    assert (resolved.source, resolved.model, resolved.name) == ("db", "glm-4.7", "第二套")
    assert model_config.resolve("image").source == "env"


def test_price_fallback_and_override(isolated_env, env_llm):
    """配置行单价空 → 回退 env；填了 → 用行内值。"""
    with session_scope() as session:
        row = _create(session, name="无单价")
        model_config.set_active(session, row)
    assert model_config.resolve("text").price_input_per_m == 0.3
    assert model_config.resolve("text").price_output_per_m == 0.5

    with session_scope() as session:
        priced = _create(session, name="有单价", model="glm-4.7",
                         price_input_per_m=1.0, price_output_per_m=2.0)
        model_config.set_active(session, priced)
    llm = model_config.resolve("text")
    assert (llm.price_input_per_m, llm.price_output_per_m) == (1.0, 2.0)


def test_thinking_tri_state(isolated_env, env_llm):
    """空=auto（glm 前缀关）/ on / off 三态。"""
    cases = [
        (None, "glm-4.7", True),
        (None, "deepseek-chat", False),
        ("on", "deepseek-chat", True),
        ("off", "glm-4.7", False),
    ]
    ids = []
    with session_scope() as session:
        for i, (flag, model, _) in enumerate(cases):
            ids.append(_create(session, name=f"t{i}", model=model, disable_thinking=flag).id)
    with session_scope() as session:
        for (flag, model, expected), rid in zip(cases, ids):
            assert model_config.resolve_row(session.get(ModelConfig, rid)).disable_thinking is expected


def test_delete_active_falls_back_to_env(isolated_env, env_llm):
    with session_scope() as session:
        row = _create(session)
        rid = row.id
        model_config.set_active(session, row)
    assert model_config.resolve("text").source == "db"
    with session_scope() as session:
        model_config.delete_config(session, session.get(ModelConfig, rid))
    assert model_config.resolve("text").source == "env"


def test_partial_unique_index_enforces_single_active(isolated_env):
    """绕过服务层直写两条 active：SQLite 部分唯一索引兜底报错。"""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            _create(session, name="a").is_active = True
            _create(session, name="b").is_active = True
            session.flush()


# ---- mock_enabled 矩阵（兼容存量测试打桩是硬要求）----

def test_mock_enabled_matrix(isolated_env, env_llm, monkeypatch):
    # 1) 显式开关（测试 patch config.LLM_MOCK=True）且 env 有 key → 强制 mock
    monkeypatch.setattr(config, "LLM_MOCK", True)
    assert model_config.mock_enabled("text") is True
    # 2) env 无 key、无 DB 配置 → 自动 mock
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "LLM_MOCK", False)
    assert model_config.mock_enabled("text") is True
    # 3) env 无 key 但页面 active 配置有 key → 真实调用（本功能的核心场景）
    with session_scope() as session:
        row = _create(session, name="页面配置")
        model_config.set_active(session, row)
    assert model_config.mock_enabled("text") is False
    assert model_config.mock_enabled("image") is True  # 图片用途仍无 key
    # 4) DB active 配置也没填 key → mock
    with session_scope() as session:
        row = _create(session, name="无key配置", api_key="")
        model_config.set_active(session, row)
    assert model_config.mock_enabled("text") is True


def test_masked_key():
    assert model_config.masked_key("abcdefgh12345678") == "abcdefgh****5678"
    assert model_config.masked_key("short") == "sh****"
    assert model_config.masked_key("") == ""


# ---- API 契约 ----

def test_api_crud_activate_and_masking(client):
    secret = "sk-plaintext-secret-999888"
    r = client.post("/api/models", json={
        "purpose": "text", "name": "GLM 主力",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "api_key": secret, "model": "glm-4.7",
        "price_input_per_m": 1.0, "price_output_per_m": 2.0, "disable_thinking": "on",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["model"] == "glm-4.7" and body["is_active"] is False
    assert body["base_url"] == "https://open.bigmodel.cn/api/paas/v4"  # 去尾斜杠
    assert body["api_key_masked"] == "sk-plain****9888" and body["has_key"] is True
    assert secret not in r.text
    cid = body["id"]

    # 列表：无 active → 回退 env；明文不出现
    data = client.get("/api/models").json()
    assert data["purposes"]["text"]["active_config_id"] is None
    assert data["purposes"]["text"]["resolved"]["source"] == "env"
    assert data["purposes"]["text"]["mock_enabled"] is False  # env key 存在
    assert secret not in json.dumps(data)

    # 激活 → resolve 切到 db
    assert client.post(f"/api/models/{cid}/activate").json()["is_active"] is True
    data = client.get("/api/models").json()
    assert data["purposes"]["text"]["active_config_id"] == cid
    assert data["purposes"]["text"]["resolved"]["model"] == "glm-4.7"

    # 再激活另一套 → 互斥（旧 active 让位）
    r = client.post("/api/models", json={
        "purpose": "text", "name": "DeepSeek 备用",
        "base_url": "https://api.deepseek.com", "api_key": "sk-ds-111222333",
        "model": "deepseek-chat"})
    cid2 = r.json()["id"]
    client.post(f"/api/models/{cid2}/activate")
    data = client.get("/api/models").json()
    assert data["purposes"]["text"]["active_config_id"] == cid2
    assert [c for c in data["configs"] if c["id"] == cid][0]["is_active"] is False

    # 编辑：api_key 留空=保持；单价传 null=清空回退 env；thinking 置空=auto
    r = client.put(f"/api/models/{cid}", json={
        "purpose": "text", "name": "GLM 主力",
        "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "",
        "model": "glm-4.6", "price_input_per_m": None, "price_output_per_m": None,
        "disable_thinking": None})
    assert r.status_code == 200
    updated = r.json()
    assert updated["model"] == "glm-4.6"
    assert updated["price_input_per_m"] is None and updated["disable_thinking"] is None
    assert updated["api_key_masked"] == "sk-plain****9888"  # key 未被动
    assert secret not in r.text

    # 删除 active → 回退 env 并提示
    r = client.delete(f"/api/models/{cid2}")
    assert r.status_code == 200
    assert r.json()["was_active"] is True and r.json()["note"]
    data = client.get("/api/models").json()
    assert data["purposes"]["text"]["active_config_id"] is None
    assert data["purposes"]["text"]["resolved"]["source"] == "env"
    # 删除备用行：无回退提示
    assert client.delete(f"/api/models/{cid}").json() == {
        "deleted": True, "was_active": False, "note": None}


def test_api_validation_errors(client):
    assert client.post("/api/models", json={
        "purpose": "video", "name": "x", "base_url": "https://x.example",
        "model": "m"}).status_code == 422
    assert client.post("/api/models", json={
        "purpose": "text", "name": "x", "base_url": "https://x.example",
        "model": "m", "disable_thinking": "maybe"}).status_code == 422
    ok = {"purpose": "text", "name": "重名", "base_url": "https://x.example",
          "api_key": "k", "model": "m"}
    assert client.post("/api/models", json=ok).status_code == 201
    assert client.post("/api/models", json=ok).status_code == 409  # 名称唯一
    assert client.post("/api/models/999/activate").status_code == 404
    assert client.delete("/api/models/999").status_code == 404
    assert client.post("/api/models/999/test").status_code == 404


def test_api_test_endpoint_no_key_and_unreachable(client):
    """连通性测试：无 key 立即失败；有 key 地址不通 → 连接错误摘要，不泄 key。"""
    r = client.post("/api/models", json={
        "purpose": "text", "name": "无key", "base_url": "http://127.0.0.1:1/v1",
        "api_key": "", "model": "m"})
    cid = r.json()["id"]
    d = client.post(f"/api/models/{cid}/test").json()
    assert d["ok"] is False and "api_key" in d["detail"] and d["billed"] is False

    secret = "sk-will-not-leak-777666"
    r = client.post("/api/models", json={
        "purpose": "text", "name": "不通", "base_url": "http://127.0.0.1:1/v1",
        "api_key": secret, "model": "m"})
    cid = r.json()["id"]
    resp = client.post(f"/api/models/{cid}/test")
    d = resp.json()
    assert d["ok"] is False and d["latency_ms"] >= 0 and len(d["detail"]) <= 200
    assert secret not in resp.text


def test_models_page_renders(client):
    r = client.get("/models")
    assert r.status_code == 200
    assert "模型配置" in r.text
    assert 'href="/models"' in r.text  # base.html 导航


# ---- generator / imagegen 接线 ----

def test_generate_uses_active_config_and_records_model(isolated_env, env_llm, monkeypatch):
    """真实路径：url/key/model/thinking 全走 DB 配置；usage.model 归因实际模型，
    成本按该配置单价折算。"""
    import httpx

    from app.schemas import WechatArticle
    from app.services import generator

    with session_scope() as session:
        row = _create(session, name="GLM 主力", model="glm-4.7",
                      price_input_per_m=1.0, price_output_per_m=2.0)
        model_config.set_active(session, row)

    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(
                {"title": "t", "digest": "d", "content_md": "# h"})},
                "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 2000}}

    class _Client:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls.update(url=url, headers=headers, payload=json)
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)

    result = generator.generate("wechat", WechatArticle, "sys", "user")
    assert result.ok, result.error
    assert result.usage["model"] == "glm-4.7"  # 实际调用模型，不是 config.MODEL_NAME
    assert result.usage["cost_est"] == round(1000 / 1e6 * 1.0 + 2000 / 1e6 * 2.0, 6)
    assert calls["url"] == "https://db.example/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer sk-db-key-123456"
    assert calls["payload"]["model"] == "glm-4.7"
    assert calls["payload"]["thinking"] == {"type": "disabled"}  # glm 前缀自动关


def test_generate_mock_fallback_without_any_key(isolated_env, monkeypatch):
    """env 无 key 且无 DB 配置 → generate 走 mock（脚手架路径原样保留）。"""
    from app.schemas import WechatArticle
    from app.services import generator

    monkeypatch.setattr(config, "LLM_MOCK", False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    result = generator.generate("wechat", WechatArticle, "sys", "user")
    assert result.ok and result.usage["model"] == "mock"


def test_imagegen_gates_and_active_config(isolated_env, env_llm, monkeypatch):
    """总闸关 → 不出图；总闸开但生效配置无 key → 不出图；active 图片配置 →
    url/key/model 独立于文案模型。"""
    from app.services import imagegen

    monkeypatch.setattr(config, "IMAGEGEN_ENABLED", False)
    assert imagegen.generate_background("p", 1080, 1440) is None

    monkeypatch.setattr(config, "IMAGEGEN_ENABLED", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")  # env 无 key 且无 DB 图片配置
    assert imagegen.generate_background("p", 1080, 1440) is None

    with session_scope() as session:
        row = model_config.create_config(
            session, purpose="image", name="cogview",
            base_url="https://img.example/v1", api_key="sk-img-key",
            model="cogview-4")
        model_config.set_active(session, row)

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, "PNG")

    import httpx

    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"url": "http://img.example/x.png"}]}

    class _ImgResp:
        content = buf.getvalue()

    class _Client:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls.update(url=url, headers=headers, payload=json)
            return _Resp()

        def get(self, url):
            return _ImgResp()

    monkeypatch.setattr(httpx, "Client", _Client)
    img = imagegen.generate_background("画面提示词", 1080, 1440)

    assert img is not None and img.size == (1080, 1440)  # 取档后缩放到画布
    assert calls["url"] == "https://img.example/v1/images/generations"
    assert calls["headers"]["Authorization"] == "Bearer sk-img-key"
    assert calls["payload"]["model"] == "cogview-4"
    assert calls["payload"]["size"] == "864x1152"  # 3:4 取 cogview 尺寸档


# ---- 成本报表按模型单价归因 ----

def test_cost_report_lists_model_prices(isolated_env, env_llm):
    from app.models import Article, Topic
    from app.services import scoring

    with session_scope() as session:
        topic = Topic(title="t", angle="", domain="AI与编程", source="radar")
        session.add(topic)
        session.flush()
        for i, model_name in enumerate(("glm-4.7", "no-config-model")):
            session.add(Article(
                topic_id=topic.id, prompt_id=None, platform="xhs",
                title=f"a{i}", content="", tags=[],
                meta={"usage": {"model": model_name, "prompt_tokens": 100,
                                "completion_tokens": 50, "cost_est": 0.001 * (i + 1)}},
                status="ready"))
        model_config.create_config(
            session, purpose="text", name="glm", base_url="https://x",
            api_key="k", model="glm-4.7",
            price_input_per_m=1.0, price_output_per_m=2.0)

    report = scoring.cost_report()
    models = {m["model"]: m for m in report["price"]["models"]}
    assert models["glm-4.7"]["price_source"] == "model_configs"
    assert (models["glm-4.7"]["input_per_m"], models["glm-4.7"]["output_per_m"]) == (1.0, 2.0)
    assert models["no-config-model"]["price_source"] == "env_default"
    assert models["no-config-model"]["input_per_m"] == 0.3
    # 历史成本数字原样聚合（不做追溯重算）
    assert report["total"]["articles"] == 2
