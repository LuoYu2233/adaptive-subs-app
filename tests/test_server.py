import http.client
import json
import threading
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
