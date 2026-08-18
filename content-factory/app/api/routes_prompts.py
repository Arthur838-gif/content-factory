"""提示词模板管理接口（P3 / M4 人工热更新入口）+ 模板效果分报表（P4）。"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from ..db import session_scope
from ..models import Prompt
from ..services import scoring

router = APIRouter(prefix="/api", tags=["prompts"])


def _prompt_dict(prompt: Prompt) -> dict:
    return {
        "id": prompt.id,
        "platform": prompt.platform,
        "name": prompt.name,
        "scenario": prompt.scenario,
        "template": prompt.template,
        "variables": prompt.variables or [],
        "version": prompt.version,
        "enabled": prompt.enabled,
        "updated_at": prompt.updated_at,
    }


class PromptCreate(BaseModel):
    platform: str = Field(..., max_length=20)
    scenario: str = Field(..., max_length=20)
    template: str = Field(..., min_length=1, max_length=65_536)  # 拦误传整个文件/超大 payload
    name: str = Field(default="", max_length=100)
    variables: list[str] = Field(default_factory=list, max_length=50)


class PromptUpdate(BaseModel):
    enabled: bool | None = None


@router.get("/prompts")
def list_prompts() -> list[dict]:
    with session_scope() as session:
        prompts = session.scalars(
            select(Prompt).order_by(Prompt.platform, Prompt.scenario, desc(Prompt.version))
        ).all()
        return [_prompt_dict(prompt) for prompt in prompts]


@router.post("/prompts", status_code=201)
def create_prompt(payload: PromptCreate) -> dict:
    """基于同 platform/scenario 的历史创建递增版本，旧版本原样保留。"""
    with session_scope() as session:
        max_version = session.scalars(
            select(Prompt.version)
            .where(Prompt.platform == payload.platform, Prompt.scenario == payload.scenario)
            .order_by(desc(Prompt.version))
            .limit(1)
        ).first()
        prompt = Prompt(
            platform=payload.platform,
            scenario=payload.scenario,
            name=payload.name or f"{payload.platform} {payload.scenario}",
            template=payload.template,
            variables=payload.variables,
            version=(max_version or 0) + 1,
            enabled=True,
        )
        session.add(prompt)
        session.flush()
        return _prompt_dict(prompt)


@router.get("/prompts/stats")
def stats_prompts() -> list[dict]:
    """模板效果分（派生报表，不落字段）：按 prompt 版本聚合已发布文章互动均值。

    published < PROMPT_STATS_MIN_SAMPLES 时 sufficient_samples=false，只展示。
    """
    return scoring.prompt_stats()


@router.put("/prompts/{prompt_id}")
def update_prompt(prompt_id: int, payload: PromptUpdate) -> dict:
    with session_scope() as session:
        prompt = session.get(Prompt, prompt_id)
        if prompt is None:
            raise HTTPException(status_code=404, detail=f"prompt {prompt_id} 不存在")
        if payload.enabled is not None:
            prompt.enabled = payload.enabled
        prompt.updated_at = datetime.now()
        session.flush()
        return _prompt_dict(prompt)
