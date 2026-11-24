"""Exercise cancellation and restart with real workers and temporary durable storage."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

import annotate as A
import server as S
from task_store import TaskStore


def subtitles(texts=("I went to the bank.", "The bank approved the loan.")):
    return {"title": "Resume example", "duration": len(texts) * 5, "source": "manual",
            "cues": [{"s": i * 5, "e": i * 5 + 5, "text": t} for i, t in enumerate(texts)]}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path / "data")
    monkeypatch.setattr(S, "EXT_DATA", tmp_path / "extension")
    monkeypatch.setattr(S.P, "client", lambda *_: object())
    monkeypatch.setattr(A, "find_candidates", lambda texts: [[] for _ in texts])
    calls = []

    def annotate(job, ids, effort):
        calls.append(("ann", list(ids)))
        with job.cond:
            job.ann.update({i: [] for i in ids})

    def translate(job, task):
        calls.append(("tr", list(task["ids"])))
        with job.cond:
            job.zh.update({i: "银行。" for i in task["ids"]})

    monkeypatch.setattr(S, "run_annotation", annotate)
    monkeypatch.setattr(S, "run_translation", translate)
    return calls


def persisted_job(sub=None):
    sub = sub or subtitles()
    job = S.Job("abcdefghijk", sub=sub)
    job.title, job.duration, job.cues = sub["title"], sub["duration"], sub["cues"]
    job.ann, job.zh = {0: []}, {0: "我去了银行。"}
    job.checkpoint()
    return job


def finish(job):
    job.thread.join(timeout=5)
    assert not job.thread.is_alive(), "worker failed to terminate"


def test_restart_reuses_completed_batches_and_context(runtime):
    persisted_job()
    S.TASK_STORE.mark_interrupted()
    assert S.TASK_STORE.load("abcdefghijk")["state"] == "interrupted"
    resumed = S.get_job("abcdefghijk", start=True)
    finish(resumed)
    assert sorted(runtime) == [("ann", [1]), ("tr", [1])]
    assert resumed.zh[0] == "我去了银行。"
    assert resumed.state == "done"
    assert S.TASK_STORE.load(resumed.vid)["state"] == "done"


def test_changed_neighbor_invalidates_contextual_results(runtime):
    persisted_job()
    resumed = S.get_job("abcdefghijk", True, sub=subtitles(("I went to the bank.", "The river was flooding.")))
    finish(resumed)
    assert sorted(runtime) == [("ann", [0, 1]), ("tr", [0, 1])]


def test_changed_model_does_not_mix_results(runtime, monkeypatch):
    persisted_job()
    monkeypatch.setattr(S.P, "model", lambda *_: "different-model")
    resumed = S.get_job("abcdefghijk", True)
    finish(resumed)
    assert sorted(runtime) == [("ann", [0, 1]), ("tr", [0, 1])]


def test_cancel_waits_for_inflight_and_preserves_results(runtime, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(S, "WORKERS", 1)

    def slow_annotation(job, ids, effort):
        entered.set()
        assert release.wait(timeout=5)
        with job.cond:
            job.ann.update({i: [] for i in ids})

    monkeypatch.setattr(S, "run_annotation", slow_annotation)
    job = S.get_job("abcdefghijk", True, sub=subtitles())
    try:
        assert entered.wait(timeout=5)
        S.cancel_job(job)
        assert job.state == "cancelling"
        with pytest.raises(S.JobBusy):
            S.get_job(job.vid, True)
    finally:
        release.set()
        finish(job)
    assert job.state == "cancelled" and runtime == []
    saved = S.TASK_STORE.load(job.vid)
    assert saved["payload"]["ann"] == {"0": [], "1": []}
    assert job.events[-1][1]["cancelled"]
    resumed = S.get_job(job.vid, True)
    finish(resumed)
    assert runtime == [("tr", [0, 1])]
    assert resumed.state == "done"


def test_failed_client_setup_does_not_leave_running_ghost(runtime, monkeypatch):
    monkeypatch.setattr(S.P, "client", Mock(side_effect=ValueError("invalid key")))
    with pytest.raises(ValueError):
        S.get_job("abcdefghijk", True, sub=subtitles())
    assert not S.JOBS


def test_active_job_limit_is_enforced_before_start(runtime):
    for i in range(S.MAX_ACTIVE_JOBS):
        S.JOBS[str(i)] = S.Job(str(i))
    with pytest.raises(S.JobBusy):
        S.get_job("abcdefghijk", True, sub=subtitles())


def test_snapshots_exclude_credentials(runtime, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-must-not-be-saved")
    persisted_job()
    assert "test-secret" not in json.dumps(S.TASK_STORE.recent())


def test_snapshot_store_handles_concurrent_writes_and_restart(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: store.save(str(i), "running", {"done": i}), range(12)))
    reopened = TaskStore(store.path)
    reopened.mark_interrupted()
    assert len(reopened.recent()) == 12
    assert all(j["state"] == "interrupted" for j in reopened.recent())
    assert reopened.load("7")["payload"] == {"done": 7}
