"""模型配置接口：文案/图片大模型的多套配置 + 「当前使用」切换（/models 页后端）。

语义（与 model_config 服务层一致）：
- 每用途（text/image）至多一条 is_active，activate 同事务互斥；
- 删掉 active 行 → 该用途下一次 resolve 回退 .env 的 OPENAI_*；
- api_key 只进库不外露：所有响应只回掩码，PUT 留空 = 保持原 key；
- /test 连通性：文案发 max_tokens=8 的极小 chat；图片发最小出图请求
  （会真实计费 1 张，页面上有标注）；错误摘要截断、绝不带 key。
"""
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from .. import config
from ..db import session_scope
from ..models import ModelConfig
from ..services import model_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelConfigIn(BaseModel):
    """创建/更新入参。purpose 只在创建时生效（更新不改用途）。"""

    purpose: str = Field(pattern="^(text|image)$", description="text=文案 / image=图片")
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=256)
    api_key: str = Field(default="", max_length=256, description="创建必填；更新留空=保持不变")
    model: str = Field(min_length=1, max_length=128)
    price_input_per_m: float | None = Field(default=None, ge=0, description="元/百万输入 token；空=回退 .env 默认")
    price_output_per_m: float | None = Field(default=None, ge=0, description="元/百万输出 token；空=回退 .env 默认")
    disable_thinking: str | None = Field(
        default=None, pattern="^(on|off)$", description="空=按模型名自动（glm 前缀关思维链）"
    )


def _get_or_404(session, config_id: int) -> ModelConfig:
    row = session.get(ModelConfig, config_id)
    if row is None:
        raise HTTPException(404, f"模型配置 {config_id} 不存在")
    return row


@router.get("")
def list_all() -> dict:
    """配置清单（key 掩码）+ 各用途当前生效解析结果（页面状态行用）。"""
    with session_scope() as session:
        configs = model_config.list_configs(session)
    purposes = {}
    for purpose in model_config.PURPOSES:
        llm = model_config.resolve(purpose)
        purposes[purpose] = {
            "label": model_config.PURPOSE_LABELS[purpose],
            "active_config_id": next(
                (c["id"] for c in configs if c["purpose"] == purpose and c["is_active"]), None
            ),
            "resolved": {
                "name": llm.name,
                "base_url": llm.base_url,
                "model": llm.model,
                "api_key_masked": model_config.masked_key(llm.api_key),
                "has_key": bool(llm.api_key),
                "source": llm.source,  # db=页面配置 / env=.env 回退
            },
            "mock_enabled": model_config.mock_enabled(purpose),
        }
    return {"configs": configs, "purposes": purposes, "imagegen_enabled": config.IMAGEGEN_ENABLED}


@router.post("", status_code=201)
def create_config(body: ModelConfigIn) -> dict:
    """新增配置（初始为备用；要生效需再调 activate）。"""
    with session_scope() as session:
        try:
            row = model_config.create_config(
                session,
                purpose=body.purpose,
                name=body.name.strip(),
                base_url=body.base_url.strip(),
                api_key=body.api_key,
                model=body.model.strip(),
                price_input_per_m=body.price_input_per_m,
                price_output_per_m=body.price_output_per_m,
                disable_thinking=body.disable_thinking,
            )
            result = model_config.config_dict(row)
        except IntegrityError as exc:
            raise HTTPException(409, f"名称「{body.name.strip()}」已存在") from exc
    logger.info("模型配置创建：%s（%s / %s）", row.name, row.purpose, row.model)
    return result


@router.put("/{config_id}")
def update_config(config_id: int, body: ModelConfigIn) -> dict:
    """更新配置（purpose 不改；api_key 留空=保持原 key，避免编辑时明文回填）。"""
    with session_scope() as session:
        row = _get_or_404(session, config_id)
        try:
            model_config.update_config(
                session,
                row,
                name=body.name.strip(),
                base_url=body.base_url.strip(),
                api_key=body.api_key,
                model=body.model.strip(),
                price_input_per_m=body.price_input_per_m,
                price_output_per_m=body.price_output_per_m,
                disable_thinking=body.disable_thinking,
            )
            result = model_config.config_dict(row)
        except IntegrityError as exc:
            raise HTTPException(409, f"名称「{body.name.strip()}」已存在") from exc
    logger.info("模型配置更新：%s（id=%s）", row.name, config_id)
    return result


@router.post("/{config_id}/activate")
def activate_config(config_id: int) -> dict:
    """设为该用途「当前使用」；下一次生成/出图即刻生效，无需重启。"""
    with session_scope() as session:
        row = _get_or_404(session, config_id)
        model_config.set_active(session, row)
        result = model_config.config_dict(row)
    logger.info("模型配置切换当前使用：%s（%s）", row.name, row.purpose)
    return result


@router.delete("/{config_id}")
def delete_config(config_id: int) -> dict:
    """删除配置；删的是当前使用行时该用途自动回退 .env。"""
    with session_scope() as session:
        row = _get_or_404(session, config_id)
        was_active = row.is_active
        purpose = row.purpose
        model_config.delete_config(session, row)
    logger.info("模型配置删除：id=%s（%s，%s）", config_id, purpose, "当前使用" if was_active else "备用")
    note = "该用途已回退 .env 默认配置" if was_active else None
    return {"deleted": True, "was_active": was_active, "note": note}


@router.post("/{config_id}/test")
def test_config(config_id: int) -> dict:
    """连通性测试（直接用该行参数，未激活的配置也能测）。

    文案：max_tokens=8 的极小 chat 请求；图片：最小出图请求（真实计费 1 张）。
    返回 {ok, latency_ms, detail, billed}；错误摘要截断且不含 api_key。
    """
    with session_scope() as session:
        row = _get_or_404(session, config_id)
        purpose = row.purpose
        llm = model_config.resolve_row(row)  # expire_on_commit=False，事务外可用
    if not llm.api_key:
        return {"ok": False, "latency_ms": 0, "detail": "该配置未填写 api_key", "billed": False}

    headers = {"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"}
    started = time.perf_counter()
    try:
        if purpose == model_config.PURPOSE_TEXT:
            payload = {
                "model": llm.model,
                "messages": [{"role": "user", "content": "回复：ok"}],
                "max_tokens": 8,
            }
            if llm.disable_thinking:
                # GLM 思维链会吃光 8 个 token，测试请求直接关掉，只测连通
                payload["thinking"] = {"type": "disabled"}
            with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
                resp = client.post(f"{llm.base_url}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                resp.json()
            return {
                "ok": True,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "detail": f"chat 接口连通（{llm.model}）",
                "billed": False,
            }
        with httpx.Client(timeout=config.IMAGEGEN_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{llm.base_url}/images/generations",
                headers=headers,
                json={
                    "model": llm.model,
                    "prompt": "纯色浅灰背景，无文字",
                    "size": "1024x1024",
                    "n": 1,
                },
            )
            resp.raise_for_status()
            resp.json()
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": f"出图接口连通（{llm.model}，本次测试已计费 1 张）",
            "billed": True,
        }
    except httpx.HTTPStatusError as exc:
        detail = f"HTTP {exc.response.status_code}: {exc.response.text[:160]}"
    except httpx.HTTPError as exc:  # 连接失败 / 超时（URL 不含 key，可安全展示）
        detail = str(exc)[:200]
    except Exception as exc:  # 响应体不是 JSON 等
        detail = str(exc)[:160]
    logger.warning("模型连通性测试失败：id=%s（%s）%s", config_id, purpose, detail)
    return {
        "ok": False,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "detail": detail,
        "billed": purpose == model_config.PURPOSE_IMAGE,
    }
