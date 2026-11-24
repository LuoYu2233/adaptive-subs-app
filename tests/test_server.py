import http.client
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import annotate as A
import server as S


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path)
    monkeypatch.setattr(S, "EXT_DATA", tmp_path / "extension")
    monkeypatch.setattr(S, "JOBS", {})
    httpd = S.Server(("127.0.0.1", 0), S.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def request(httpd, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


H = {"X-Adaptive-Subs": "1", "Content-Type": "application/json"}


@pytest.mark.parametrize("path", ["/api/open?url=abcdefghijk", "/api/focus?v=abcdefghijk&t=4", "/api/stream?v=abcdefghijk", "/api/status"])
def test_api_requires_custom_header(http_server, path):
    assert request(http_server, "GET", path)[0] == 403


def test_get_never_starts_paid_work(http_server, monkeypatch):
    start = Mock()
    monkeypatch.setattr(S, "get_job", start)
    assert request(http_server, "GET", "/api/open?url=abcdefghijk", headers=H)[0] == 405
    start.assert_not_called()


def test_foreign_origin_rejected_even_with_header(http_server):
    assert request(http_server, "POST", "/api/open", '{"url":"abcdefghijk"}', {**H, "Origin": "https://evil.example"})[0] == 403


def test_health_remains_public(http_server):
    status, body = request(http_server, "GET", "/api/health")
    assert status == 200 and json.loads(body)["ok"]


def test_backup_preview_and_restore_are_protected_and_revision_checked(http_server, monkeypatch):
    monkeypatch.setattr(S.PS, "base_known", lambda *_: (set(), {}))
    S.PROFILE.mark("bank", "known")
    assert request(http_server, "GET", "/api/profile/export")[0] == 403
    status, raw = request(http_server, "GET", "/api/profile/export", headers=H)
    assert status == 200
    backup = json.loads(raw)
    before = S.PROFILE.path.read_bytes()
    body = json.dumps({"backup": backup, "expected_rev": 1})
    assert request(http_server, "POST", "/api/profile/import/preview", body, H)[0] == 200
    assert S.PROFILE.path.read_bytes() == before
    assert request(http_server, "POST", "/api/profile/import", body)[0] == 403
    assert request(http_server, "POST", "/api/profile/clear", '{"expected_rev":0}', H)[0] == 409
    assert request(http_server, "POST", "/api/profile/clear", '{"expected_rev":1}', H)[0] == 200
    assert S.PROFILE.export_backup()["profile"] == {}
    assert request(http_server, "POST", "/api/profile/import", json.dumps({"backup": backup, "expected_rev": 2}), H)[0] == 200
    assert S.PROFILE.load()["known"] == ["bank"]


def test_post_starts_job(http_server, monkeypatch):
    start = Mock()
    monkeypatch.setattr(S, "get_job", start)
    monkeypatch.setattr(S.P, "has_key", lambda: True)
    status, body = request(http_server, "POST", "/api/open", '{"url":"abcdefghijk"}', H)
    assert status == 200 and not json.loads(body)["cached"]
    start.assert_called_once_with("abcdefghijk", start=True)


@pytest.mark.parametrize("body", ['{"t":NaN}', '{"t":"Infinity"}', '{"t":{}}'])
def test_invalid_focus_cannot_poison_scheduler(http_server, body):
    assert request(http_server, "POST", "/api/focus", body, H)[0] == 400


def job(tmp_path):
    j = S.Job("abcdefghijk", save_dir=tmp_path)
    j.title, j.duration = "test", 5
    j.cues = [{"s": 0, "e": 5, "text": "The river bank."}]
    j.texts = ["The river bank."]
    return j


def test_failed_work_cannot_be_reported_complete(tmp_path, monkeypatch):
    j = job(tmp_path)
    j.tasks = [{"kind": "ann", "ids": [0]}]
    j.total = 1
    monkeypatch.setattr(S, "run_annotation", Mock(side_effect=A.ModelResponseError("failed")))
    S.worker(j)
    S.finalize(j)
    assert j.state == "partial"
    assert j.failed_tasks and j.events[-1][0] == "fail"
    assert j.events[-1][1]["retry"]
    saved = json.loads((tmp_path / "abcdefghijk.json").read_text())
    assert saved["status"] == "partial" and saved["cues"][0]["text"]
    assert S.saved_result(tmp_path / "abcdefghijk.json") is None


def test_legitimately_empty_annotations_can_complete(tmp_path):
    j = job(tmp_path)
    j.ann, j.zh = {0: []}, {0: "河岸。"}
    S.finalize(j)
    assert j.state == "done" and j.events[-1][0] == "done"
    assert S.saved_result(tmp_path / "abcdefghijk.json")["status"] == "complete"


def test_partial_saved_video_can_be_retried(http_server, monkeypatch):
    j = job(S.DATA)
    S.finalize(j)
    start = Mock()
    monkeypatch.setattr(S, "get_job", start)
    monkeypatch.setattr(S.P, "has_key", lambda: True)
    status, body = request(http_server, "POST", "/api/open", '{"url":"abcdefghijk"}', H)
    assert status == 200 and not json.loads(body)["cached"]
    start.assert_called_once()


def test_nonfinite_subtitle_timestamps_are_dropped():
    assert S.clean_ext_cues([{"s": 0, "e": float("inf"), "text": "bad"}]) == []


def test_job_capacity_returns_actionable_http_error(http_server, monkeypatch):
    monkeypatch.setattr(S.P, "has_key", lambda: True)
    monkeypatch.setattr(S, "get_job", Mock(side_effect=S.JobBusy("请先完成或取消一个任务")))
    status, body = request(http_server, "POST", "/api/open", '{"url":"abcdefghijk"}', H)
    assert status == 409 and json.loads(body)["retry"]


def test_cancellation_endpoint_preserves_job(http_server):
    j = job(S.DATA)
    S.JOBS[j.vid] = j
    status, body = request(http_server, "POST", "/api/jobs/cancel", json.dumps({"id": j.vid}), H)
    assert status == 200 and json.loads(body)["state"] == "cancelling"
    assert j.aborted and S.TASK_STORE.load(j.vid)["state"] == "cancelling"


@pytest.mark.parametrize("path", ["/api/jobs/cancel", "/api/jobs/resume"])
def test_job_mutations_require_header(http_server, path):
    assert request(http_server, "POST", path, '{"id":"abcdefghijk"}', {"Content-Type": "application/json"})[0] == 403


def test_probe_never_repairs_old_asr_with_billable_work(http_server, monkeypatch):
    S.EXT_DATA.mkdir()
    vid = "yt_abcdefghijk_asr"
    (S.EXT_DATA / f"{vid}.json").write_text(json.dumps({"status": "complete", "source": "asr", "video": {"id": vid},
        "cues": [{"text": "The request was rejected because it was considered high risk."}]}))
    start, repair = Mock(return_value=None), Mock()
    monkeypatch.setattr(S, "get_job", start)
    monkeypatch.setattr(S, "repair_saved", repair)
    monkeypatch.setattr(S.P, "has_key", lambda *_: True)
    monkeypatch.setattr(S.ASR, "configured", lambda: True)
    status, body = request(http_server, "POST", "/api/ext/open", json.dumps({"id": vid, "source": "asr", "probe": True}), H)
    assert status == 200 and json.loads(body)["cached"] is False
    start.assert_called_once_with(vid, start=False)
    repair.assert_not_called()


def test_long_cues_are_preserved_instead_of_silently_truncated():
    text = "a " * 300
    cues = S.clean_ext_cues([{"s": 0, "e": 5, "text": text}])
    assert cues[0]["text"] == text.strip()


@pytest.mark.parametrize("cues", [
    [{"s": 0, "e": 1, "text": "line"}] * (S.MAX_CUES + 1),
    [{"s": 0, "e": 1, "text": "x" * (S.MAX_CUE_CHARS + 1)}],
])
def test_oversized_subtitles_are_rejected_before_a_job_starts(http_server, monkeypatch, cues):
    start = Mock()
    monkeypatch.setattr(S, "get_job", start)
    monkeypatch.setattr(S.P, "has_key", lambda *_: True)
    status, body = request(http_server, "POST", "/api/ext/open", json.dumps({"id": "bili_test", "cues": cues}), H)
    assert status == 400 and json.loads(body)["error"]
    start.assert_not_called()


def test_model_configuration_is_saved_and_returned(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "CONFIG", tmp_path / "settings.env")
    # Track environment mutations made by save_config so they are restored after this test.
    for key in ("DEEPSEEK_MODEL", "ASR_MODEL"):
        monkeypatch.setenv(key, "old")
    status, body = request(http_server, "POST", "/api/settings", json.dumps({"models": {"deepseek": "custom-model"}, "asr_model": "custom-asr"}), H)
    assert status == 200 and json.loads(body)["ok"]
    assert S.P.model("deepseek") == "custom-model"
    settings = json.loads(request(http_server, "GET", "/api/settings", headers=H)[1])
    assert next(p for p in settings["providers"] if p["id"] == "deepseek")["model"] == "custom-model"
    assert settings["asr"]["model"] == "custom-asr"


def test_model_names_cannot_inject_config_lines(http_server, monkeypatch):
    save = Mock()
    monkeypatch.setattr(S, "save_config", save)
    status, _ = request(http_server, "POST", "/api/settings", json.dumps({"models": {"deepseek": "model\nMIMO_API_KEY=bad"}}), H)
    assert status == 400
    save.assert_not_called()


def test_connection_check_distinguishes_missing_model_and_asr(http_server, monkeypatch):
    service = Mock()
    service.with_options.return_value.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id="mimo-v2.5-asr")])
    monkeypatch.setattr(S.P, "has_key", lambda *_: True)
    monkeypatch.setattr(S.P, "client", lambda *_: service)
    status, body = request(http_server, "POST", "/api/settings/test", "{}", H)
    result = json.loads(body)["results"]
    assert status == 200 and result["asr"] == "ok"
    assert "模型列表中没有" in result["deepseek"] and "模型列表中没有" in result["mimo"]
    service.chat.completions.create.assert_not_called()
