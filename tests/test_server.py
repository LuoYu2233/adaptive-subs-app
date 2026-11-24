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


@pytest.mark.parametrize("body", [
    {"word": "bank", "sentence": None}, {"word": ["bank"], "sentence": "The bank."},
    {"word": "bank", "sentence": "The riverbank."}, {"word": "bank", "sentence": "x" * 2000 + " bank"},
    {"word": "x" * 41, "sentence": "x" * 41},
])
def test_bad_explanation_input_never_requests_a_model(http_server, monkeypatch, body):
    call = Mock(side_effect=AssertionError("Must not request a model"))
    monkeypatch.setattr(A, "call_json", call)
    status, raw = request(http_server, "POST", "/api/explain", json.dumps(body), H)
    assert status == 400 and "原句" in json.loads(raw)["error"]
    call.assert_not_called()


def test_explanation_preserves_clicked_word_after_first_400_characters(http_server, monkeypatch):
    service = object()
    result = {"zh": "河岸", "pos": "n.", "base": "bank", "note": ""}
    call = Mock(return_value=result)
    monkeypatch.setattr(S.P, "has_key", lambda: True)
    monkeypatch.setattr(S, "client", lambda: service)
    monkeypatch.setattr(A, "call_json", call)
    sentence = "We continued our walk. " * 30 + "We reached the river bank."
    status, raw = request(http_server, "POST", "/api/explain", json.dumps({"word": "bank", "sentence": sentence}), H)
    assert status == 200 and json.loads(raw) == result
    assert call.call_args.args[4]["sentence"] == sentence
    assert call.call_args.args[0] is service


def test_extension_receives_platform_restriction_without_model_work(http_server, monkeypatch):
    error = S.video_access_error(RuntimeError("Sign in to confirm you're not a bot"))
    monkeypatch.setattr(S, "fetch_subtitles", Mock(side_effect=error))
    status, raw = request(http_server, "POST", "/api/ext/open", json.dumps({
        "id": "abcdefghijk", "source": "youtube", "subs_only": True}), H)
    body = json.loads(raw)
    assert status == 200 and body["code"] == "platform_verification" and body["retry"]
    assert not S.JOBS and not body.get("nosubs")


def test_foreign_origin_rejected_even_with_header(http_server):
    assert request(http_server, "POST", "/api/open", '{"url":"abcdefghijk"}', {**H, "Origin": "https://evil.example"})[0] == 403


def test_health_remains_public(http_server):
    status, body = request(http_server, "GET", "/api/health")
    assert status == 200 and json.loads(body)["ok"]


def test_folder_open_is_protected_and_returns_a_copyable_path_when_unavailable(http_server, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "HOME", tmp_path / "new user folder")
    open_folder = Mock(side_effect=S.FolderOpenError("当前没有桌面，可以复制路径后手动打开"))
    monkeypatch.setattr(S, "open_folder", open_folder)
    assert request(http_server, "POST", "/api/open-folder", '{"what":"data"}', {"Content-Type": "application/json"})[0] == 403
    open_folder.assert_not_called()
    assert request(http_server, "POST", "/api/open-folder", '{"what":"../outside"}', H)[0] == 400
    status, raw = request(http_server, "POST", "/api/open-folder", '{"what":"data"}', H)
    result = json.loads(raw)
    assert status == 501 and "复制路径" in result["error"]
    assert result["path"] == str(S.HOME / "data")
    assert (S.HOME / "data").is_dir()
    open_folder.assert_called_once_with(S.HOME / "data")


def test_missing_word_library_preserves_profile_and_keeps_status_and_backup_available(http_server, monkeypatch):
    monkeypatch.setattr(S.LOCAL_ASR, "available", lambda: False)
    monkeypatch.setattr(S.PS, "base_known", Mock(side_effect=ImportError("synthetic missing wordfreq")))
    raw = b'{"rev":2,"known":["bank"],"identity":"undergrad"}'
    S.PROFILE.path.write_bytes(raw)
    status, body = request(http_server, "GET", "/api/status", headers=H)
    result = json.loads(body)
    assert status == 200 and "词库" in result["profile"]["error"]
    assert result["profile"]["recoverable"] is False
    status, body = request(http_server, "GET", "/api/profile", headers=H)
    assert status == 503 and json.loads(body)["recoverable"] is False
    status, body = request(http_server, "GET", "/api/profile/export", headers=H)
    backup = json.loads(body)
    assert status == 200 and backup["profile"]["known"] == ["bank"]
    for path, value in (("/api/mark", {"key": "bank", "state": "unknown"}),
                        ("/api/profile", {"level": 5000}), ("/api/profile/clear", {"expected_rev": 2}),
                        ("/api/profile/import", {"backup": backup, "expected_rev": 2})):
        assert request(http_server, "POST", path, json.dumps(value), H)[0] == 503
        assert S.PROFILE.path.read_bytes() == raw
    S.PROFILE.path.write_bytes(b"broken profile")
    assert request(http_server, "POST", "/api/profile/recover", json.dumps({"fingerprint": "a" * 64}), H)[0] == 503
    assert S.PROFILE.path.read_bytes() == b"broken profile"


def test_status_remains_available_when_recent_video_title_is_invalid(http_server, monkeypatch):
    monkeypatch.setattr(S.LOCAL_ASR, "available", lambda: False)
    monkeypatch.setattr(S.PS, "base_known", lambda *_: (set(), {}))
    value = {"video": {"id": "abcdefghijk", "title": "\ud800", "duration": 1},
        "cues": [{"s": 0, "e": 1, "text": "Original sample.", "notes": []}]}
    raw = json.dumps(value).encode()
    (S.DATA / "abcdefghijk.json").write_bytes(raw)
    status, body = request(http_server, "GET", "/api/status", headers=H)
    assert status == 200 and json.loads(body)["recent"] == []
    assert (S.DATA / "abcdefghijk.json").read_bytes() == raw


@pytest.mark.parametrize("path,policy", [
    ("/", "strict-origin-when-cross-origin"), ("/player.html?v=abcdefghijk", "strict-origin-when-cross-origin"),
    ("/profile", "no-referrer"), ("/setup", "no-referrer"), ("/api/health", "no-referrer"),
])
def test_referrer_policy_allows_only_player_origin_for_youtube(http_server, path, policy):
    connection = http.client.HTTPConnection(*http_server.server_address, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Referrer-Policy") == policy
        assert response.getheader("X-Frame-Options") == "DENY"
        response.read()
    finally:
        connection.close()


def test_task_corruption_keeps_status_and_profile_usable_and_recovery_is_protected(http_server, monkeypatch):
    monkeypatch.setattr(S.LOCAL_ASR, "available", lambda: False)
    monkeypatch.setattr(S.PS, "base_known", lambda *_: (set(), {}))
    raw = b"damaged task database"
    S.TASK_STORE.path.write_bytes(raw)
    status, body = request(http_server, "GET", "/api/status", headers=H)
    issue = json.loads(body)["task_issue"]
    assert status == 200 and issue["recoverable"]
    assert request(http_server, "GET", "/api/profile", headers=H)[0] == 200
    body = json.dumps({"fingerprint": issue["fingerprint"]})
    assert request(http_server, "POST", "/api/jobs/recover", body)[0] == 403
    assert S.TASK_STORE.path.read_bytes() == raw
    j = job(S.DATA)
    S.JOBS[j.vid] = j
    assert request(http_server, "POST", "/api/jobs/recover", body, H)[0] == 409
    S.JOBS.clear()
    assert request(http_server, "POST", "/api/jobs/recover", body, H)[0] == 200
    assert S.TASK_STORE.recent() == []
    assert next((S.TASK_STORE.path.parent / "recovery").iterdir()).read_bytes() == raw


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


def test_failed_catalog_write_does_not_turn_saved_generation_into_failure(http_server, monkeypatch):
    j = job(S.DATA)
    j.ann[0], j.zh[0] = [], "河岸。"
    monkeypatch.setattr(A, "update_index", Mock(side_effect=OSError("index write failed")))
    S.finalize(j)
    assert j.state == "done" and j.events[-1][0] == "done"
    assert "目录" in j.events[-1][1]["warning"]
    assert S.saved_result(S.DATA / "abcdefghijk.json") is not None
    status, raw = request(http_server, "GET", "/data/index.json")
    assert status == 200 and any(row["id"] == "abcdefghijk" for row in json.loads(raw))


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


@pytest.mark.parametrize("raw", [
    b'[' * 2000 + b'0' + b']' * 2000,
    json.dumps({"video": {"id": "abcdefghijk", "title": "Original", "duration": 1},
        "cues": [{"s": 0, "e": 1, "text": "invalid \ud800", "notes": []}]}).encode(),
])
def test_unreadable_result_returns_retry_without_starting_work(http_server, monkeypatch, raw):
    path = S.DATA / "abcdefghijk.json"
    path.write_bytes(raw)
    start = Mock(side_effect=AssertionError("Reading cannot generate"))
    monkeypatch.setattr(S, "get_job", start)
    status, body = request(http_server, "GET", "/data/abcdefghijk.json")
    assert status == 409 and json.loads(body)["retry"] is True
    assert path.read_bytes() == raw
    start.assert_not_called()


def test_nonfinite_subtitle_timestamps_are_dropped():
    assert S.clean_ext_cues([{"s": 0, "e": float("inf"), "text": "bad"}]) == []


def test_unpaired_unicode_cannot_reach_profile_storage(http_server):
    status, _ = request(http_server, "POST", "/api/profile", '{"field":"\\ud800"}', H)
    assert status == 400 and not S.PROFILE.path.exists()


def test_oversized_numeric_focus_is_rejected(http_server):
    assert request(http_server, "POST", "/api/focus", json.dumps({"t": 10 ** 400}), H)[0] == 400


def test_corrupt_profile_has_protected_non_destructive_recovery(http_server, monkeypatch):
    monkeypatch.setattr(S.PS, "base_known", lambda *_: (set(), {}))
    raw = b'{"known":["saved-record",'
    S.PROFILE.path.write_bytes(raw)
    status, body = request(http_server, "GET", "/api/profile", headers=H)
    assert status == 503 and json.loads(body)["recoverable"]
    fingerprint = json.loads(body)["fingerprint"]
    assert request(http_server, "POST", "/api/mark", '{"key":"bank","state":"known"}', H)[0] == 503
    assert S.PROFILE.path.read_bytes() == raw
    body = json.dumps({"fingerprint": fingerprint})
    assert request(http_server, "POST", "/api/profile/recover", body)[0] == 403
    assert request(http_server, "POST", "/api/profile/recover", body, H)[0] == 200
    assert S.PROFILE.export_backup()["profile"] == {}
    assert next((S.PROFILE.path.parent / "recovery").iterdir()).read_bytes() == raw


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
    (S.EXT_DATA / f"{vid}.json").write_text(json.dumps({"status": "complete", "source": "asr", "video": {"id": vid, "title": "test", "duration": 5},
        "cues": [{"s": 0, "e": 5, "notes": [], "text": "The request was rejected because it was considered high risk."}]}))
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
    status, body = request(http_server, "POST", "/api/settings", json.dumps({"expected_rev": S.SETTINGS_REV, "models": {"deepseek": "custom-model"}, "asr_model": "custom-asr"}), H)
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
    assert json.loads(body)["provider"] == "deepseek"
    assert json.loads(body)["checked_models"]["asr"] == "mimo-v2.5-asr"
    assert "模型列表中没有" in result["deepseek"] and "模型列表中没有" in result["mimo"]
    service.chat.completions.create.assert_not_called()


def test_settings_acknowledges_saved_snapshot_without_returning_keys(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "CONFIG", tmp_path / "settings.env")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-old-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "before")
    status, raw = request(http_server, "POST", "/api/settings", json.dumps({
        "expected_rev": S.SETTINGS_REV, "keys": {"deepseek": "synthetic-new-key-1234"}, "models": {"deepseek": "after"}}), H)
    body = json.loads(raw)
    assert status == 200 and body["ok"]
    assert body["settings"] == json.loads(request(http_server, "GET", "/api/settings", headers=H)[1])
    provider = next(p for p in body["settings"]["providers"] if p["id"] == "deepseek")
    assert provider["model"] == "after" and provider["key_hint"].endswith("1234）")
    assert b"synthetic-new-key" not in raw and b"synthetic-old-key" not in raw


def test_settings_disk_failure_preserves_file_and_runtime_config(http_server, monkeypatch, tmp_path):
    path = tmp_path / "settings.env"
    original = b"DEEPSEEK_MODEL=before\n"
    path.write_bytes(original)
    monkeypatch.setattr(S, "CONFIG", path)
    monkeypatch.setenv("DEEPSEEK_MODEL", "before")
    monkeypatch.setattr(S, "atomic_write", Mock(side_effect=OSError("simulated full disk")))
    reset = Mock()
    monkeypatch.setattr(S.P, "reset_clients", reset)
    before_rev = S.SETTINGS_REV
    status, _ = request(http_server, "POST", "/api/settings", json.dumps({"expected_rev": before_rev, "models": {"deepseek": "after"}}), H)
    assert status == 507 and path.read_bytes() == original and S.P.model("deepseek") == "before"
    assert S.SETTINGS_REV == before_rev
    reset.assert_not_called()


def test_stale_settings_save_preserves_other_windows_changes(http_server, monkeypatch, tmp_path):
    path = tmp_path / "settings.env"
    monkeypatch.setattr(S, "CONFIG", path)
    monkeypatch.setenv("DEEPSEEK_MODEL", "original-model")
    monkeypatch.setenv("ANNOTATION_PROVIDER", "deepseek")
    first = json.loads(request(http_server, "GET", "/api/settings", headers=H)[1])
    stale = {"expected_rev": first["rev"], "provider": "mimo", "models": {p["id"]: p["model"] for p in first["providers"]}}
    status, raw = request(http_server, "POST", "/api/settings", json.dumps({
        "expected_rev": first["rev"], "models": {"deepseek": "new-model"}}), H)
    assert status == 200
    assert json.loads(raw)["settings"]["rev"] != first["rev"]
    before = path.read_bytes()
    status, raw = request(http_server, "POST", "/api/settings", json.dumps(stale), H)
    assert status == 409 and json.loads(raw)["conflict"]
    assert path.read_bytes() == before and S.P.model("deepseek") == "new-model" and S.P.current() == "deepseek"


@pytest.mark.parametrize("revision", [None, "", "previous-process-token", "0" * 32, ["wrong-type"]])
def test_settings_writes_require_current_edit_token(http_server, monkeypatch, revision):
    save = Mock()
    monkeypatch.setattr(S, "save_config", save)
    status, raw = request(http_server, "POST", "/api/settings", json.dumps({
        "expected_rev": revision, "models": {"deepseek": "new-model"}}), H)
    assert status == 409 and json.loads(raw)["conflict"]
    save.assert_not_called()


def test_simultaneous_settings_writers_cannot_both_use_one_revision(http_server, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "settings.env"
    monkeypatch.setattr(S, "CONFIG", path)
    monkeypatch.setenv("DEEPSEEK_MODEL", "before")
    token = S.SETTINGS_REV
    barrier = threading.Barrier(2)
    def save(model):
        barrier.wait(timeout=3)
        status, _ = request(http_server, "POST", "/api/settings", json.dumps({
            "expected_rev": token, "models": {"deepseek": model}}), H)
        return status, model
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first-model", "second-model"]))
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(model for status, model in results if status == 200)
    assert S.P.model("deepseek") == winner and f"DEEPSEEK_MODEL={winner}" in path.read_text()


@pytest.mark.parametrize("asr_url,cache_version,warning", [("https://www.youtube.com/watch?v=abcdefghijk", None, True),
    ("https://www.youtube.com/watch?v=abcdefghijk", 2, False), (None, None, False)])
def test_old_speech_task_discloses_cache_reprocessing_before_resume(http_server, asr_url, cache_version, warning):
    S.TASK_STORE.save("yt_abcdefghijk_asr", "interrupted", {
        "title": "Original pending task", "asr_url": asr_url, "asr_cache_version": cache_version})
    status, raw = request(http_server, "GET", "/api/status", headers=H)
    assert status == 200
    task = next(job for job in json.loads(raw)["jobs"] if job["id"] == "yt_abcdefghijk_asr")
    assert task["cache_update"] is warning
