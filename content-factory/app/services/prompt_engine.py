"""M4 Prompt 策略引擎（计划书第 7 章 M4 / SDD 3.2 M4）。

职责：按 platform + scenario 选模板、用 Jinja2 渲染变量，产出 system / user 消息对。
边界：不写死任何文案、不调 LLM、不进程缓存模板——每次现读库，改库即对下次生成生效。
模板里禁止出现 Python 逻辑（Jinja 禁用 for/if 之外的扩展），故用沙箱环境渲染。

种子入库（计划书 M4 / SDD 8.3）：以 platform + scenario + version 为幂等键，
首次入库后已存在即跳过，重启服务绝不覆盖库内已修改的模板。
"""
import logging
from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.exceptions import UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import desc, select

from .. import config
from ..db import session_scope
from ..models import Prompt

logger = logging.getLogger(__name__)

# 种子文件目录与文件名约定（计划书第 3 章：prompts/*.yml）
SEED_DIR = Path(config.PROJECT_ROOT / "prompts")
SEED_FILES = ["wechat_article.yml", "xhs_note.yml", "xhs_teardown.yml"]  # P0 A2 / P1 A1 / P-1b A3

# 沙箱渲染：禁用除 Jinja 内置 for/if 之外的扩展，禁止访问对象属性/危险调用
_env = SandboxedEnvironment(
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


class PromptNotFoundError(LookupError):
    """指定 platform + scenario（或 prompt_id）下找不到可用模板。"""


class TemplateRenderError(ValueError):
    """模板渲染失败（变量缺失或 Jinja 语法错误）。"""


def _split_template(template: str) -> tuple[str, str]:
    """按 "# system" / "# user" 标记把模板拆成两段。

    种子模板（附录 A2）形如：
        # system
        <system 段>
        # user
        <user 段>
    仅以 "#" 开头的行可作标记（正文里恰好是 "system"/"user" 的普通行不会被误判）；
    标记行允许前后空白；缺任一段时返回空串。
    """
    system_lines: list[str] = []
    user_lines: list[str] = []
    target: list[str] | None = None
    for line in template.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            marker = stripped.lstrip("#").strip().lower()
            if marker == "system":
                target = system_lines
                continue
            if marker == "user":
                target = user_lines
                continue
        if target is not None:
            target.append(line)
    # 去掉尾部空行
    return "\n".join(system_lines).rstrip(), "\n".join(user_lines).rstrip()


def _render(tpl: str, variables: dict) -> str:
    if not tpl:
        return ""
    try:
        return _env.from_string(tpl).render(**variables)
    except UndefinedError as exc:
        raise TemplateRenderError(f"模板变量缺失：{exc}") from exc
    except Exception as exc:  # Jinja 语法错误等
        raise TemplateRenderError(f"模板渲染失败：{exc}") from exc


def select_prompt(session, platform: str, scenario: str, prompt_id: int | None = None) -> Prompt:
    """取模板：传 prompt_id 用指定模板；否则取 enabled 中 version 最大的一条。

    prompt_id 必须属于请求的 platform/scenario——跨场景套用模板会因变量不匹配
    在渲染层炸出 500，在这里直接 409 拦下。
    """
    if prompt_id is not None:
        prompt = session.get(Prompt, prompt_id)
        if prompt is None:
            raise PromptNotFoundError(f"prompt_id={prompt_id} 不存在")
        if prompt.platform != platform or prompt.scenario != scenario:
            raise PromptNotFoundError(
                f"prompt_id={prompt_id} 属于 {prompt.platform}/{prompt.scenario}，"
                f"不能用于 {platform}/{scenario}"
            )
        return prompt
    prompt = session.scalars(
        select(Prompt)
        .where(Prompt.platform == platform, Prompt.scenario == scenario, Prompt.enabled.is_(True))
        .order_by(desc(Prompt.version))
        .limit(1)
    ).first()
    if prompt is None:
        raise PromptNotFoundError(
            f"无可用模板：platform={platform} scenario={scenario}（无 enabled 记录）"
        )
    return prompt


def render_messages(
    session,
    platform: str,
    scenario: str,
    variables: dict,
    prompt_id: int | None = None,
) -> tuple[Prompt, str, str]:
    """选模板 → 拆分 → 渲染，返回 (prompt, system_message, user_message)。

    每次现读库不缓存：验收点"在数据库改 Prompt 文案不重启即对下次生成生效"由此保证。
    """
    prompt = select_prompt(session, platform, scenario, prompt_id)
    system_tpl, user_tpl = _split_template(prompt.template)
    system_message = _render(system_tpl, variables)
    user_message = _render(user_tpl, variables)
    return prompt, system_message, user_message


def seed_prompts(db_path=None) -> list[str]:
    """从 prompts/*.yml 种子文件入库（幂等键 platform + scenario + version）。

    已存在即跳过，绝不覆盖库内已修改模板；返回新插入的幂等键列表。
    在 app 启动 lifespan 中调用，也可手动调用。重复运行安全。
    """
    inserted: list[str] = []
    if not SEED_DIR.exists():
        logger.warning("种子目录不存在：%s", SEED_DIR)
        return inserted

    with session_scope(db_path) as session:
        for name in SEED_FILES:
            path = SEED_DIR / name
            if not path.exists():
                logger.warning("种子文件缺失：%s", path)
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            platform = str(data.get("platform", ""))
            scenario = str(data.get("scenario", ""))
            version = int(data.get("version", 1))
            key = f"{platform}+{scenario}+v{version}"

            exists = session.scalars(
                select(Prompt.id).where(
                    Prompt.platform == platform,
                    Prompt.scenario == scenario,
                    Prompt.version == version,
                )
            ).first()
            if exists:
                continue  # 幂等：已存在即跳过，重启不覆盖

            session.add(
                Prompt(
                    platform=platform,
                    name=str(data.get("name", "")),
                    scenario=scenario,
                    template=str(data.get("template", "")),
                    variables=data.get("variables"),
                    version=version,
                    enabled=bool(data.get("enabled", True)),
                )
            )
            inserted.append(key)
            logger.info("入库种子模板：%s", key)

    return inserted
