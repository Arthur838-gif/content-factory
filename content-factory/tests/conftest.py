"""pytest 配置：本仓库的验收脚本是独立可执行脚本（.venv/Scripts/python tests/test_pN.py），
模块级即改 config 指向临时库，被 pytest 收集会互相污染且报 ImportError。
在装了 pytest 的环境里运行 `pytest tests/` 时跳过这些脚本，仅收集真正的 pytest 用例。"""

collect_ignore = [
    "test_p0.py",
    "test_p1.py",
    "test_p1a.py",
    "test_p1b.py",
    "test_p2.py",
    "test_p3.py",
    "test_p4.py",
    "alert_receiver.py",
    "_run_real_acceptance.py",
    "verify_p1a_live.sh",
]
