"""验收脚本共用支撑（非 pytest 用例，conftest 不收集）。

P-2 词表入库后，各脚本在 config 指向临时库、init_db() 之后，
统一经 seed_domains_from(fixture) 导入领域种子（幂等）；
不再复制/改写共享 YAML fixture。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DOMAINS = PROJECT_ROOT / "tests" / "fixtures" / "domains.test.yml"


def seed_domains_from(yml_path: Path | None = None) -> dict:
    """领域种子导入（在 config.DB_PATH 指向临时库后调用，幂等）。"""
    from app.db import session_scope
    from app.services import domain_service

    with session_scope() as session:
        return domain_service.seed_domains(session, Path(yml_path or FIXTURE_DOMAINS))
