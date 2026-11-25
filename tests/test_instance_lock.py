import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from instance_lock import InstanceLock, read_instance
import server as S


def test_process_lock_is_exclusive_and_metadata_is_not_the_lock(tmp_path):
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert first.acquire()
    try:
        first.publish(S.VERSION, "a" * 32, "127.0.0.1", 8766)
        assert read_instance(tmp_path)["port"] == 8766
        assert not second.acquire()
        # Corrupt metadata cannot release the OS lock or permit a second server.
        (tmp_path / ".instance.json").write_text("broken", encoding="utf-8")
        assert read_instance(tmp_path) is None
        assert not second.acquire()
    finally:
        first.close()
    assert second.acquire()
    second.close()


def test_killed_owner_releases_lock_without_deleting_any_runtime_file(tmp_path):
    code = """import sys
from pathlib import Path
from instance_lock import InstanceLock
lock=InstanceLock(Path(sys.argv[1]))
assert lock.acquire()
print('locked',flush=True)
sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1] / "app", stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    contender = InstanceLock(tmp_path)
    try:
        assert process.stdout.readline().strip() == "locked"
        assert not contender.acquire()
        process.kill()
        process.wait(timeout=5)
        assert (tmp_path / ".instance.lock").exists()
        assert contender.acquire()
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        contender.close()
        process.stdin.close()
        process.stdout.close()


@pytest.mark.parametrize("change", [{"host": "outside.invalid"}, {"port": 0}, {"port": True}, {"instance": "bad"}])
def test_invalid_runtime_metadata_is_not_used_for_navigation(tmp_path, change):
    (tmp_path / ".instance.json").write_text(json.dumps({"version": S.VERSION, "instance": "a" * 32,
        "host": "127.0.0.1", "port": 8766, **change}), encoding="utf-8")
    assert read_instance(tmp_path) is None


@pytest.mark.parametrize("version", [S.VERSION, "0.3.0"])
def test_repeated_start_uses_verified_owner_and_reports_version_conflict(tmp_path, monkeypatch, version):
    owner = InstanceLock(tmp_path)
    assert owner.acquire()
    try:
        owner.publish(version, "a" * 32, "127.0.0.1", 9123)
        monkeypatch.setattr(S, "HOME", tmp_path)
        monkeypatch.setattr(S, "probe_server", Mock(return_value={"version": version, "instance": "a" * 32}))
        window = Mock()
        monkeypatch.setattr(S, "run_window", window)
        args = SimpleNamespace(host="localhost", gui=True)
        if version == S.VERSION:
            S.reuse_instance(args, Mock(), False)
            window.assert_called_once_with("http://127.0.0.1:9123/app", None)
        else:
            with pytest.raises(RuntimeError, match="请先退出"): S.reuse_instance(args, Mock(), False)
            window.assert_not_called()
    finally:
        owner.close()


def test_wrong_process_token_never_opens_a_window(tmp_path, monkeypatch):
    owner = InstanceLock(tmp_path)
    assert owner.acquire()
    try:
        owner.publish(S.VERSION, "a" * 32, "127.0.0.1", 9123)
        monkeypatch.setattr(S, "HOME", tmp_path)
        monkeypatch.setattr(S, "probe_server", Mock(return_value={"version": S.VERSION, "instance": "b" * 32}))
        window = Mock()
        monkeypatch.setattr(S, "run_window", window)
        clock = iter([0, 0, 4])
        monkeypatch.setattr(S.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(S.time, "sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="尚未确认连接"):
            S.reuse_instance(SimpleNamespace(host="127.0.0.1", gui=True), Mock(), False)
        window.assert_not_called()
    finally:
        owner.close()


def test_failed_metadata_publish_releases_process_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "HOME", tmp_path)
    monkeypatch.setattr(S.A, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(S.applog, "install", lambda *args: None)
    monkeypatch.setattr(InstanceLock, "publish", Mock(side_effect=OSError("disk full")))
    monkeypatch.setattr(sys, "argv", ["server.py"])
    with pytest.raises(OSError, match="disk full"): S.main()
    replacement = InstanceLock(tmp_path)
    assert replacement.acquire()
    replacement.close()
    assert S.INSTANCE_GUARD is None
