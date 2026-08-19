"""统一测试入口（P-2）：pytest 正式用例 + 全部离线验收脚本，任一失败非零退出。

用法（content-factory 目录下）：
    .venv/Scripts/python tests/run_all.py    # Windows / Git Bash
    .venv/bin/python tests/run_all.py        # Linux / macOS

不含 tests/_run_real_acceptance.py（真实 LLM，按 token 计费）：
需要时显式手动运行，见 README「真实质量验收」。
"""
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS = PROJECT_ROOT / "tests"

# 离线验收脚本（各自临时库隔离，全部不联网不付费；顺序 = 依赖分层从底到上）
ACCEPTANCE_SCRIPTS = [
    "test_p0.py",      # 选题→生成→落库主干（LLM mock）
    "test_p1.py",      # 小红书文案结构
    "test_p1a.py",     # 热榜采集 + 手动触发
    "test_p1b.py",     # 低粉爆款采样 + 熔断（本地 mock mcp）
    "test_redfox.py",  # RedFox 响应解析（文档示例桩，不联网）
    "test_p2.py",      # 图片合成
    "test_p3.py",      # 预览页 + 素材包
    "test_p4.py",      # 回填报表
    "test_p6.py",      # 提示词库
    "test_pillar.py",  # 内容栏目 + 周排期 + 采样入队
    "test_discovery.py",  # 领域发现（RedFox 离线桩）
    "test_github.py",  # GitHub 采集（离线桩）
]

PER_SCRIPT_TIMEOUT = 600


def run(title: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 60}\n▶ {title}\n{'=' * 60}", flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=PER_SCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"✗ {title} 超时（>{PER_SCRIPT_TIMEOUT}s）")
        return False
    seconds = time.monotonic() - started
    if result.returncode == 0:
        print(f"✓ {title}（{seconds:.0f}s）")
        return True
    print(f"✗ {title} 退出码 {result.returncode}（{seconds:.0f}s）")
    return False


def main() -> int:
    python = sys.executable
    outcomes: list[tuple[str, bool]] = []

    outcomes.append(("pytest 正式用例", run(
        "pytest 正式用例（迁移 / 领域 / 采样任务 / lifespan / 安全渲染）",
        [python, "-m", "pytest", "tests", "-q"],
    )))
    for script in ACCEPTANCE_SCRIPTS:
        path = TESTS / script
        if not path.is_file():
            print(f"！ 跳过缺失脚本：{script}")
            continue
        outcomes.append((script, run(script, [python, str(path)])))

    print(f"\n{'=' * 60}\n汇总\n{'=' * 60}")
    failed = [name for name, ok in outcomes if not ok]
    for name, ok in outcomes:
        print(f"  {'✓' if ok else '✗'} {name}")
    if failed:
        print(f"\nFAIL：{len(failed)} 项未通过")
        return 1
    print(f"\nPASS：{len(outcomes)} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
