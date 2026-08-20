"""模型配置服务：文案/图片大模型运行时切换（/models 页 + generator/imagegen 调用）。

解析链：purpose（text/image）的 is_active 行 → 无则回退 .env 的 OPENAI_*。
每次 LLM 调用前查一次库（一次 SELECT，相比秒级调用可忽略），页面切换即刻
生效于下一次生成，无缓存失效问题。

mock 判定（mock_enabled）：
- 显式开关优先——env CF_LLM_MOCK=1 或测试 monkeypatch config.LLM_MOCK；
- 否则当前生效配置（DB active 或 env 回退）有 api_key 就真实调用，无 key
  自动 mock。兼容存量测试打桩，也覆盖「env 没配 key 但页面配了模型」。

安全：api_key 明文存本地库（data/ 已 gitignore 的单机工具），对外（API/页面/
日志）只出掩码。
"""
from dataclasses import dataclass

from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..models import ModelConfig

PURPOSE_TEXT = "text"
PURPOSE_IMAGE = "image"
PURPOSES = (PURPOSE_TEXT, PURPOSE_IMAGE)
PURPOSE_LABELS = {PURPOSE_TEXT: "文案", PURPOSE_IMAGE: "图片"}


@dataclass(frozen=True)
class ResolvedLLM:
    """一次 LLM 调用的生效参数（model 进 usage 记账，单价进成本估算）。"""

    purpose: str
    name: str
    base_url: str
    api_key: str
    model: str
    price_input_per_m: float
    price_output_per_m: float
    disable_thinking: bool
    source: str  # db=页面配置 / env=.env 回退


def _env_fallback(purpose: str) -> ResolvedLLM:
    model = config.MODEL_NAME if purpose == PURPOSE_TEXT else config.GLM_IMAGE_MODEL
    return ResolvedLLM(
        purpose=purpose,
        name=".env 回退",
        base_url=config.OPENAI_BASE_URL,
        api_key=config.OPENAI_API_KEY,
        model=model,
        price_input_per_m=config.LLM_PRICE_INPUT_PER_M,
        price_output_per_m=config.LLM_PRICE_OUTPUT_PER_M,
        disable_thinking=_auto_thinking(model),
        source="env",
    )


def _auto_thinking(model: str) -> bool:
    """GLM 系模型默认关思维链（产物是结构化 JSON，思考段纯烧 token）。"""
    return (model or "").lower().startswith("glm")


def _thinking_flag(row: ModelConfig) -> bool:
    if row.disable_thinking == "on":
        return True
    if row.disable_thinking == "off":
        return False
    return _auto_thinking(row.model)


def resolve_row(row: ModelConfig) -> ResolvedLLM:
    """任意配置行 → 生效参数（连通性测试未激活的配置也用得上）；单价空回退 env。"""
    return ResolvedLLM(
        purpose=row.purpose,
        name=row.name,
        base_url=row.base_url,
        api_key=row.api_key,
        model=row.model,
        price_input_per_m=(
            row.price_input_per_m
            if row.price_input_per_m is not None
            else config.LLM_PRICE_INPUT_PER_M
        ),
        price_output_per_m=(
            row.price_output_per_m
            if row.price_output_per_m is not None
            else config.LLM_PRICE_OUTPUT_PER_M
        ),
        disable_thinking=_thinking_flag(row),
        source="db",
    )


def resolve(purpose: str) -> ResolvedLLM:
    """当前生效配置：purpose 的 active 行，无则回退 .env。"""
    if purpose not in PURPOSES:
        raise ValueError(f"未知用途 {purpose!r}（可选：{PURPOSE_TEXT}/{PURPOSE_IMAGE}）")
    with session_scope() as session:
        row = session.scalars(
            select(ModelConfig).where(ModelConfig.purpose == purpose, ModelConfig.is_active)
        ).first()
        if row is None:
            return _env_fallback(purpose)
        return resolve_row(row)


def mock_enabled(purpose: str) -> bool:
    """显式开关（env CF_LLM_MOCK / 测试 patch config.LLM_MOCK）优先强制 mock；
    否则当前生效配置有 key 就真实调用，无 key 自动 mock。"""
    if config.LLM_MOCK and config.OPENAI_API_KEY:
        # env 显式 CF_LLM_MOCK=1（配了 key 仍要求 mock）或测试 monkeypatch
        return True
    return not resolve(purpose).api_key


def masked_key(key: str) -> str:
    """ak_ab12cd****ef34：头 8 后 4 可见，中间打码；短 key 只出头 2 位。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:8]}****{key[-4:]}"


def config_dict(row: ModelConfig) -> dict:
    """API/页面行视图：key 只出掩码，绝不回明文。"""
    return {
        "id": row.id,
        "purpose": row.purpose,
        "purpose_label": PURPOSE_LABELS.get(row.purpose, row.purpose),
        "name": row.name,
        "base_url": row.base_url,
        "api_key_masked": masked_key(row.api_key),
        "has_key": bool(row.api_key),
        "model": row.model,
        "price_input_per_m": row.price_input_per_m,
        "price_output_per_m": row.price_output_per_m,
        "disable_thinking": row.disable_thinking,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_configs(session) -> list[dict]:
    rows = session.scalars(
        select(ModelConfig).order_by(ModelConfig.purpose, ModelConfig.is_active.desc(), ModelConfig.id)
    ).all()
    return [config_dict(r) for r in rows]


def create_config(
    session,
    purpose: str,
    name: str,
    base_url: str,
    api_key: str,
    model: str,
    price_input_per_m: float | None = None,
    price_output_per_m: float | None = None,
    disable_thinking: str | None = None,
) -> ModelConfig:
    row = ModelConfig(
        purpose=purpose,
        name=name,
        base_url=base_url.rstrip("/"),
        api_key=api_key.strip(),
        model=model,
        price_input_per_m=price_input_per_m,
        price_output_per_m=price_output_per_m,
        disable_thinking=disable_thinking or None,
        is_active=False,
    )
    session.add(row)
    session.flush()
    return row


def update_config(
    session,
    row: ModelConfig,
    *,
    name: str,
    base_url: str,
    api_key: str = "",
    model: str,
    price_input_per_m: float | None = None,
    price_output_per_m: float | None = None,
    disable_thinking: str | None = None,
) -> ModelConfig:
    """全量更新（编辑表单整行提交）：单价 / 思维链传 None 即清空、回退 env 默认
    ——换模型后旧单价不残留，成本归因不失真。唯一例外 api_key：留空=保持不变
    （编辑表单不回填明文 key）。purpose 不在参数里，创建后不可改用途。"""
    row.name = name
    row.base_url = base_url.rstrip("/")
    if api_key:  # 留空=保持不变
        row.api_key = api_key.strip()
    row.model = model
    row.price_input_per_m = price_input_per_m
    row.price_output_per_m = price_output_per_m
    row.disable_thinking = disable_thinking or None
    session.flush()
    return row


def set_active(session, row: ModelConfig) -> None:
    """设为该用途「当前使用」：同用途旧 active 置 False（同事务互斥）。"""
    for other in session.scalars(
        select(ModelConfig).where(
            ModelConfig.purpose == row.purpose, ModelConfig.is_active, ModelConfig.id != row.id
        )
    ).all():
        other.is_active = False
    row.is_active = True
    session.flush()


def delete_config(session, row: ModelConfig) -> None:
    """删除配置；删的是 active 行时该用途自动回退 .env（下一次 resolve 生效）。"""
    session.delete(row)
    session.flush()


def price_for(model_name: str) -> tuple[float, float] | None:
    """按模型名查单价（成本报表归因用）；配置行没填单价或查不到返回 None。"""
    with session_scope() as session:
        row = session.scalars(
            select(ModelConfig).where(ModelConfig.model == model_name)
        ).first()
        if row and row.price_input_per_m is not None and row.price_output_per_m is not None:
            return row.price_input_per_m, row.price_output_per_m
    return None
