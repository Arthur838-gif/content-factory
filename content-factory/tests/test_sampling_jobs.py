"""sampling_jobs 队列与 worker 测试：入队/去重/领取/逐词进度/空结果/失败/熔断/
租约回收/取消/重试/API 契约。fetch_keyword 全部打桩（conftest.stub_fetch），不联网。"""
from datetime import datetime, timedelta

import pytest

from app import config
from app.db import session_scope
from app.models import CollectorState, HotItem, SamplingJob
from app.services import sampling_jobs
from app.services.worker import SamplingWorker


def _note_count() -> int:
    with session_scope() as s:
        return s.query(HotItem).count()


def _collector_state() -> CollectorState | None:
    with session_scope() as s:
        row = s.query(CollectorState).filter_by(name="xhs_sample").first()
        return row


def test_enqueue_dedupe_and_release(isolated_env):
    job, created = sampling_jobs.enqueue(kind="manual", keywords=["AI工具", "AI写作"])
    assert created and job.total_queries == 2 and job.dedupe_key == "manual:xhs_sample"
    # 活跃同键 → 复用，不入第二条（防连点重复计费）
    again, created2 = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    assert created2 is False and again.id == job.id
    # 终态后同键可重新入队（dedupe_key 保留在历史行上供审计）
    sampling_jobs.claim_next()  # finish_job 仅 running 可终结，先领取
    assert sampling_jobs.finish_job(job.id, "succeeded") is True
    fresh, created3 = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    assert created3 and fresh.id != job.id
    with session_scope() as s:
        keys = s.query(SamplingJob).filter(SamplingJob.dedupe_key == "manual:xhs_sample").count()
        assert keys == 2  # 历史任务的键不抹除


def test_enqueue_default_keywords_from_wordlist(seeded_env):
    job, created = sampling_jobs.enqueue(kind="scheduled")
    assert created and job.total_queries > 0
    assert all(job.keywords)


def test_enqueue_rejects_empty(isolated_env):
    with pytest.raises(ValueError):
        sampling_jobs.enqueue(kind="manual", keywords=["  "])


def test_claim_next_atomic(isolated_env):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具", "AI写作"])
    assert sampling_jobs.claim_next() == job.id
    row = sampling_jobs.get_job(job.id)
    assert row.status == "running" and row.attempts == 1
    assert row.current_keyword == "AI工具"  # 指向第一个待执行词
    assert row.lease_expires_at is not None
    # 无排队任务 → None
    assert sampling_jobs.claim_next() is None


def test_worker_per_keyword_progress_and_counters(seeded_env, stub_fetch):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具", "AI写作"])
    outcome = SamplingWorker().run_once()
    assert outcome == "succeeded"
    row = sampling_jobs.get_job(job.id)
    assert row.completed_queries == 2 and row.fetched == 4 and row.inserted == 4
    assert row.meta["sources"] == {"AI工具": "redfox", "AI写作": "redfox"}
    assert _note_count() == 4
    # 成功清零失败计数
    state = _collector_state()
    assert state is None or state.consecutive_failures == 0


def test_worker_failure_keeps_prior_keyword_data(seeded_env, stub_fetch):
    """单词失败：已提交的关键词素材与进度保留（崩溃最多丢当前词）。"""
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具", "BAD", "AI写作"])
    assert SamplingWorker().run_once() == "failed"
    row = sampling_jobs.get_job(job.id)
    assert row.status == "failed" and row.error_type == "RuntimeError"
    assert row.completed_queries == 1 and row.inserted == 2  # 第 1 词已落库
    assert stub_fetch == ["AI工具", "BAD"]  # 第 3 词未执行
    state = _collector_state()
    assert state is not None and state.consecutive_failures == 1


def test_worker_succeeded_empty_no_circuit(isolated_env, stub_fetch):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["EMPTY"])
    assert SamplingWorker().run_once() == "succeeded_empty"
    row = sampling_jobs.get_job(job.id)
    assert row.status == "succeeded_empty" and row.fetched == 0
    state = _collector_state()
    assert state is None or state.consecutive_failures == 0  # 空结果不是故障


def test_worker_blocked_when_circuit_open(isolated_env, stub_fetch, monkeypatch):
    monkeypatch.setattr(config, "COLLECTOR_CIRCUIT_FAILURES", 1)
    from app.collectors import base as collectors_base

    collectors_base.record_failure("xhs_sample", "预热熔断")
    assert collectors_base.circuit_open("xhs_sample")
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    assert SamplingWorker().run_once() == "blocked"
    row = sampling_jobs.get_job(job.id)
    assert row.status == "blocked" and stub_fetch == []  # 未发起任何采样


def test_cancel_queued_job(isolated_env):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    ok, reason = sampling_jobs.cancel_job(job.id)
    assert ok and reason == "canceled"
    ok2, reason2 = sampling_jobs.cancel_job(job.id)
    assert ok2 is False and reason2 == "canceled"  # 终态不可重复取消，原因=当前状态


def test_retry_terminal_keeps_progress(isolated_env, stub_fetch):
    job, _ = sampling_jobs.enqueue(kind="manual", keywords=["BAD"])
    SamplingWorker().run_once()  # failed
    retried, outcome = sampling_jobs.retry_job(job.id)
    assert outcome == "ok" and retried.status == "queued"
    assert SamplingWorker().run_once() == "failed"
    row = sampling_jobs.get_job(job.id)
    assert row.attempts == 2  # 重试沿用同一行计数
    # 活跃任务重试 → conflict 口径
    job2, _ = sampling_jobs.enqueue(kind="manual", keywords=["AI工具"])
    _, outcome2 = sampling_jobs.retry_job(job2.id)
    assert outcome2 == "active"


def test_requeue_stale_lease(isolated_env, stub_fetch, monkeypatch):
    monkeypatch.setattr(config, "WORKER_JOB_MAX_ATTEMPTS", 2)

    def _make_running(job_id_holder, attempts, completed):
        with session_scope() as s:
            row = SamplingJob(
                kind="manual", status="running", keywords=["AI工具", "AI写作"],
                total_queries=2, completed_queries=completed,
                requested_at=datetime.now(), started_at=datetime.now(),
                attempts=attempts, dedupe_key=f"stale-{attempts}-{completed}",
                lease_expires_at=datetime.now() - timedelta(seconds=10),
            )
            s.add(row)
            s.flush()
            job_id_holder.append(row.id)

    ids: list[int] = []
    _make_running(ids, attempts=1, completed=0)
    assert sampling_jobs.requeue_stale() == 1
    row = sampling_jobs.get_job(ids[0])
    assert row.status == "queued" and row.error_type == "lease_expired"
    assert row.completed_queries == 0  # 进度保留，续跑只补剩余词

    ids2: list[int] = []
    _make_running(ids2, attempts=2, completed=1)  # 达到最大尝试次数
    assert sampling_jobs.requeue_stale() == 1
    row2 = sampling_jobs.get_job(ids2[0])
    assert row2.status == "failed" and row2.error_type == "lease_expired"


def test_worker_resumes_only_pending_keywords(isolated_env, stub_fetch):
    with session_scope() as s:
        row = SamplingJob(
            kind="manual", status="running", keywords=["AI工具", "AI写作"],
            total_queries=2, completed_queries=1,
            requested_at=datetime.now(), started_at=datetime.now(),
            attempts=1, dedupe_key="resume-1",
            lease_expires_at=datetime.now() - timedelta(seconds=10),
        )
        s.add(row)
        s.flush()
        job_id = row.id
    sampling_jobs.requeue_stale()
    stub_fetch.clear()
    assert SamplingWorker().run_once() == "succeeded"
    row = sampling_jobs.get_job(job_id)
    assert row.completed_queries == 2
    assert stub_fetch == ["AI写作"]  # 只补未完成的关键词


def test_sampling_api_contract(seeded_env, stub_fetch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    r = client.post("/api/sampling/jobs", json={"collector": "hotboard"})
    assert r.status_code == 422  # 采样任务暂只支持 xhs_sample

    r = client.post("/api/sampling/jobs", json={"collector": "xhs_sample"})
    assert r.status_code == 202 and r.json()["created"] is True
    job_id = r.json()["job"]["id"]

    # 同键活跃 → 去重复用
    r = client.post("/api/sampling/jobs", json={"collector": "xhs_sample"})
    assert r.status_code == 202 and r.json()["created"] is False

    r = client.get("/api/sampling/jobs")
    assert r.status_code == 200 and any(j["id"] == job_id for j in r.json()["jobs"])

    r = client.get(f"/api/sampling/jobs/{job_id}")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert client.get("/api/sampling/jobs/99999").status_code == 404

    # 跑完 → 终态；终态可取消可重试，运行中/排队中两个动作都被 409 挡住
    assert SamplingWorker().run_once() == "succeeded"
    r = client.post(f"/api/sampling/jobs/{job_id}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"  # 终态重试回队
    r = client.post(f"/api/sampling/jobs/{job_id}/retry")
    assert r.status_code == 409  # 排队中不可重试
    r = client.post(f"/api/sampling/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    r = client.post(f"/api/sampling/jobs/{job_id}/cancel")
    assert r.status_code == 409  # 终态不可重复取消

    # 状态总览：内嵌 worker 已关（fixture），最近任务可见
    r = client.get("/api/sampling/status")
    assert r.status_code == 200
    data = r.json()
    assert data["embedded_worker"] is False and data["active_count"] == 0
    assert data["latest"]["id"] == job_id


def test_manual_collector_trigger_queues(seeded_env, stub_fetch):
    """旧入口 POST /api/collectors/xhs_sample/run：改为 202 入队（兼容存量调用方）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/collectors/xhs_sample/run")
    assert r.status_code == 202
    data = r.json()
    assert data["queued"] is True and data["job"]["status"] == "queued"
    assert stub_fetch == []  # API 进程不执行付费网络请求
