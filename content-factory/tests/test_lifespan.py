"""lifespan 启停、调度器注册、内嵌 worker 配置与页面契约 / 安全渲染。"""
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.db import session_scope
from app.models import Prompt
from app.services import sampling_jobs
from app.services.scheduler import create_scheduler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "app" / "templates"


def test_lifespan_seeds_and_serves(isolated_env):
    """with TestClient：lifespan 跑迁移引导 + 领域/模板种子，页面全部可渲染。"""
    from app.main import app
    from app.services import domain_service

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        # 领域种子（fixture 词表 3 自定义 + 24 官方）与提示词模板已入库
        names = [d["name"] for d in domain_service.list_domains()]
        assert "AI与编程" in names and "学习教育" in names and len(names) == 27
        with session_scope() as s:
            assert s.query(Prompt).count() > 0
        for path in ("/", "/pillars", "/viral", "/stats", "/prompts"):
            assert client.get(path).status_code == 200, path


def test_scheduler_registration_matrix(isolated_env, monkeypatch):
    """调度器注册矩阵：xhs 定时开 → 6 个任务；关 → 少 xhs_sample 一个。"""
    monkeypatch.setattr(config, "XHS_SAMPLE_SCHEDULED", True)
    ids = {j.id for j in create_scheduler().get_jobs()}
    assert ids == {"hotboard", "expire_topics", "xhs_sample", "xhs_teardown", "backup", "cleanup"}

    monkeypatch.setattr(config, "XHS_SAMPLE_SCHEDULED", False)
    ids = {j.id for j in create_scheduler().get_jobs()}
    assert ids == {"hotboard", "expire_topics", "xhs_teardown", "backup", "cleanup"}


def test_embedded_worker_executes_queued_job(seeded_env, monkeypatch, stub_fetch):
    """WORKER_EMBEDDED=1：lifespan 起内嵌线程，入队任务被自动领取执行到终态。"""
    from app.main import app

    monkeypatch.setattr(config, "WORKER_EMBEDDED", True)
    monkeypatch.setattr(config, "WORKER_POLL_SECONDS", 0.05)

    with TestClient(app):
        job, created = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
        assert created
        deadline = time.monotonic() + 10
        row = None
        while time.monotonic() < deadline:
            row = sampling_jobs.get_job(job.id)
            if row.status in sampling_jobs.TERMINAL_STATUSES:
                break
            time.sleep(0.05)
        assert row is not None and row.status == "succeeded", row and row.status
        assert row.completed_queries == 1 and row.inserted == 2
    # with 退出触发 lifespan 停机：内嵌 worker 已停（再入队不会被执行）
    job2, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI写作"])
    time.sleep(0.3)
    assert sampling_jobs.get_job(job2.id).status == "queued"


def test_worker_cli_once(seeded_env):
    """独立 worker 命令行 --once：空队列 idle 退出 0；任务失败退出 1。

    子进程无法吃到进程内打桩——用空 RedFox key 强制走 mcp 分支，
    mcp 指向拒绝连接的本机端口，失败路径全程不联网。
    """
    # 子进程无法吃到进程内打桩——两个 RedFox key 环境变量都清空（.env 里任一存在
    # 都会激活付费分支），强制走 mcp；mcp 指向拒绝连接的本机端口，失败路径全程不联网
    env = {**os.environ, "CF_DB_PATH": str(config.DB_PATH),
           "CF_REDFOX_API_KEY": "", "REDFOX_API_KEY": "",
           "XHS_MCP_BASE_URL": "http://127.0.0.1:9"}
    cmd = [sys.executable, "-m", "app.services.worker", "--once"]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                       cwd=str(PROJECT_ROOT), env=env)
    assert r.returncode == 0, r.stderr
    assert "idle" in r.stdout

    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                       cwd=str(PROJECT_ROOT), env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    row = sampling_jobs.get_job(job.id)
    assert row.status == "failed" and row.completed_queries == 0


def test_page_contracts_and_safe_rendering(seeded_env, monkeypatch):
    """页面契约：/viral 任务卡、工作台最近任务、/pillars 建栏返回 job_id。"""
    from app.main import app

    client = TestClient(app)
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])

    viral = client.get("/viral").text
    assert "采样任务" in viral and "排队中" in viral
    assert "手动采样一轮（xhs_sample，入队执行）" in viral
    assert f"<td>{job.id}</td>" in viral

    home = client.get("/").text
    assert "最近任务" in home

    pillars = client.get("/pillars").text
    assert 'id="domain-options"' in pillars and "AI与编程" in pillars

    # 建栏目入队自动采样：返回 sampling_job_id 供页面轮询（不再猜素材数）
    monkeypatch.setattr(config, "PILLAR_AUTO_SAMPLE", True)
    r = client.post("/api/pillars", json={
        "name": "契约栏目", "domain": "AI与编程", "slots_per_week": 1,
        "keywords": ["AI工具"], "active": True})
    assert r.status_code == 201 and isinstance(r.json()["sampling_job_id"], int)
    # 材材数端点只报素材储备，不冒充任务进度
    pillar_id = r.json()["id"]
    m = client.get(f"/api/pillars/{pillar_id}/materials").json()
    assert set(m) == {"matched", "min_required"}


def test_templates_no_innerhtml():
    """安全渲染不变量：模板一律 DOM 节点 / textContent，零 innerHTML。

    外部数据（RedFox 笔记标题、对标账号、LLM 改写建议）经 innerHTML 拼接
    即 XSS 注入面；此用例守住回归。
    """
    offenders = [
        p.name for p in TEMPLATES.glob("*.html")
        if "innerHTML" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
