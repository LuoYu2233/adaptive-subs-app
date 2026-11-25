import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

import task_store as T


@pytest.mark.parametrize("raw", [b"not a database", b"", b"SQLite format 3\x00broken"])
def test_corrupt_task_database_is_preserved_and_can_be_rebuilt(tmp_path, raw):
    path = tmp_path / "tasks.sqlite3"
    path.write_bytes(raw)
    store = T.TaskStore(path)
    for operation in (store.recent, lambda: store.recent(summary=True), lambda: store.load("x"), lambda: store.save("x", "running", {})):
        with pytest.raises(T.TaskStoreCorrupt) as error:
            operation()
        assert error.value.fingerprint == hashlib.sha256(raw).hexdigest()
        assert path.read_bytes() == raw
    backup = Path(store.recover(error.value.fingerprint))
    assert backup.read_bytes() == raw
    assert store.recent() == []
    store.save("new", "running", {"done": 1})
    assert store.load("new")["payload"] == {"done": 1}


@pytest.mark.parametrize("payload", ['{broken', '[]', '{"title":null}', '{"started":NaN}', '{"total":[]}', '{"usage":null}',
                                     '{"ann":[]}', '{"cues":{}}', '{"zh":false}', '{"failed_tasks":null}'])
def test_invalid_snapshot_cannot_break_status_or_be_overwritten(tmp_path, payload):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    store.save("x", "running", {"done": 1})
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE jobs SET payload = ?", (payload,))
    before = store.path.read_bytes()
    for operation in (store.recent, lambda: store.recent(summary=True), lambda: store.load("x"), lambda: store.save("x", "running", {})):
        with pytest.raises(T.TaskStoreCorrupt):
            operation()
        assert store.path.read_bytes() == before


def test_existing_incompatible_schema_is_not_modified(tmp_path):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TABLE jobs (private_data TEXT)")
    before = store.path.read_bytes()
    with pytest.raises(T.TaskStoreCorrupt):
        store.recent()
    assert store.path.read_bytes() == before


def test_recovery_rejects_changed_or_healthy_database(tmp_path):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    store.path.write_bytes(b"bad original")
    fingerprint = store.fingerprint()
    store.path.write_bytes(b"bad changed")
    with pytest.raises(T.TaskStoreConflict):
        store.recover(fingerprint)
    store.path.unlink()
    store.save("x", "running", {})
    with pytest.raises(T.TaskStoreConflict):
        store.recover(store.fingerprint())
    assert store.load("x")


def test_failed_backup_preserves_database(tmp_path, monkeypatch):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    store.path.write_bytes(b"bad original")
    monkeypatch.setattr(T, "atomic_copy", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        store.recover(store.fingerprint())
    assert store.path.read_bytes() == b"bad original"


def test_failed_replacement_preserves_database_and_backup(tmp_path, monkeypatch):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    raw = b"bad original"
    store.path.write_bytes(raw)
    replace = T.os.replace
    def fail_target(source, destination):
        if Path(destination) == store.path:
            raise OSError("replacement failed")
        return replace(source, destination)
    monkeypatch.setattr(T.os, "replace", fail_target)
    with pytest.raises(OSError):
        store.recover(store.fingerprint())
    assert store.path.read_bytes() == raw
    assert next((tmp_path / "recovery").iterdir()).read_bytes() == raw


def test_pending_transaction_files_prevent_unsafe_reset(tmp_path):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    store.path.write_bytes(b"bad original")
    fingerprint = store.fingerprint()
    Path(str(store.path) + "-journal").write_bytes(b"original transaction")
    assert store.fingerprint() is None
    with pytest.raises(T.TaskStoreConflict):
        store.recover(fingerprint)
    assert store.path.read_bytes() == b"bad original"


def test_summary_reads_keep_metadata_and_full_resume_data_remains_available(tmp_path):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    payload = {"version": 1, "title": "Original title", "started": 1000, "done": 3, "total": 4,
               "asr_url": "https://example.invalid/video", "duration": 5,
               "cues": [{"s": 0, "e": 5, "text": "Original test sentence."}], "ann": {"0": []},
               "zh": {"0": "原创测试句。"}, "usage": {}, "asr_usage": {}, "failed_tasks": [], "sub": {"cues": []}}
    store.save("x", "interrupted", payload)
    before = store.path.read_bytes()
    summary = store.recent(summary=True)[0]
    assert summary["payload"] == {k: v for k, v in payload.items() if k not in ("cues", "ann", "zh", "usage", "asr_usage", "failed_tasks", "sub")}
    assert summary["id"] == "x" and summary["state"] == "interrupted"
    assert store.load("x")["payload"] == payload
    assert store.path.read_bytes() == before


def test_summary_reads_fall_back_when_sqlite_json_is_unavailable(tmp_path, monkeypatch):
    store = T.TaskStore(tmp_path / "tasks.sqlite3")
    store.save("x", "running", {"title": "Original", "done": 1})
    connect = store._connect
    attempts = []
    class WithoutJson:
        def __init__(self):
            self.connection = connect()
        def execute(self, query, args):
            if "json_valid" in query:
                attempts.append(query)
                raise sqlite3.OperationalError("no such function: json_valid")
            return self.connection.execute(query, args)
        def close(self):
            self.connection.close()
    monkeypatch.setattr(store, "_connect", WithoutJson)
    assert store.recent(summary=True)[0]["payload"]["title"] == "Original"
    assert store.recent(summary=True)[0]["payload"]["done"] == 1
    assert len(attempts) == 1
