"""统一外发告警出口（计划书第 7 章横切约定）。

格式统一为 [级别] 模块 - 事件 - 摘要；P-1a 选定 Server酱 兼容 Webhook
（NOTIFY_WEBHOOK 配置任一接收 JSON POST 的地址即可）。
外发失败只记日志，绝不打断调用方；未配置 webhook 时降级为日志告警。

手动演练：python -m app.services.notify WARN test 通道演练 "P-1a 验收"
"""
import logging
import sys
from datetime import datetime

import httpx

from .. import config

logger = logging.getLogger(__name__)


def send_alert(level: str, module: str, event: str, summary: str = "") -> bool:
    title = f"[{level}] {module} - {event}"
    desp = f"{summary}\n\n---\ncontent-factory · {datetime.now():%Y-%m-%d %H:%M:%S}"
    webhook = config.NOTIFY_WEBHOOK
    if not webhook:
        logger.warning("NOTIFY_WEBHOOK 未配置，告警仅记日志：%s %s", title, summary)
        return False
    try:
        resp = httpx.post(webhook, json={"title": title, "desp": desp}, timeout=10)
        resp.raise_for_status()
        logger.info("告警已外发：%s %s", title, summary)
        return True
    except Exception as exc:
        logger.error("告警外发失败 %r：%s %s", exc, title, summary)
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("用法: python -m app.services.notify <LEVEL> <MODULE> <EVENT> [SUMMARY]")
        return 2
    level, module, event = argv[0], argv[1], argv[2]
    summary = " ".join(argv[3:])
    ok = send_alert(level, module, event, summary)
    print("已外发" if ok else "未外发（看上方日志原因）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
