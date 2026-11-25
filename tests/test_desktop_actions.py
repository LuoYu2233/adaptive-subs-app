import subprocess
import threading
from unittest.mock import Mock

import pytest

import desktop_actions as D


def test_headless_environment_returns_an_actionable_error_without_spawning(tmp_path, monkeypatch):
    monkeypatch.setattr(D.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    spawn = Mock(side_effect=AssertionError("No desktop"))
    monkeypatch.setattr(D.subprocess, "Popen", spawn)
    with pytest.raises(D.FolderOpenError, match="复制路径"): D.open_folder(tmp_path)
    spawn.assert_not_called()


@pytest.mark.parametrize("platform,command", [("darwin", "open"), ("linux", "xdg-open")])
def test_folder_names_are_literal_arguments_not_shell_commands(tmp_path, monkeypatch, platform, command):
    folder = tmp_path / "folder with spaces; $(not-executed)"
    folder.mkdir()
    monkeypatch.setattr(D.sys, "platform", platform)
    monkeypatch.setenv("DISPLAY", ":test")
    find = Mock(return_value=f"/test/bin/{command}")
    spawn = Mock(return_value=Mock(wait=Mock(return_value=0)))
    monkeypatch.setattr(D.shutil, "which", find)
    monkeypatch.setattr(D.subprocess, "Popen", spawn)
    D.open_folder(folder)
    find.assert_called_once_with(command)
    assert spawn.call_args.args[0] == [f"/test/bin/{command}", str(folder.resolve())]
    assert spawn.call_args.kwargs.get("shell", False) is False


def test_windows_uses_native_folder_open_with_full_path(tmp_path, monkeypatch):
    monkeypatch.setattr(D.sys, "platform", "win32")
    launch = Mock()
    monkeypatch.setattr(D.os, "startfile", launch, raising=False)
    D.open_folder(tmp_path)
    launch.assert_called_once_with(str(tmp_path.resolve()))


def test_missing_launcher_and_immediate_failure_do_not_report_success(tmp_path, monkeypatch):
    monkeypatch.setattr(D.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "test")
    monkeypatch.setattr(D.shutil, "which", lambda _: None)
    with pytest.raises(D.FolderOpenError, match="没有找到"): D.open_folder(tmp_path)
    monkeypatch.setattr(D.shutil, "which", lambda _: "/test/xdg-open")
    monkeypatch.setattr(D.subprocess, "Popen", Mock(return_value=Mock(wait=Mock(return_value=3))))
    with pytest.raises(D.FolderOpenError, match="未能打开"): D.open_folder(tmp_path)
    with pytest.raises(D.FolderOpenError, match="不存在"): D.open_folder(tmp_path / "missing")


def test_long_lived_file_manager_is_reaped_without_blocking_request(tmp_path, monkeypatch):
    monkeypatch.setattr(D.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(D.shutil, "which", lambda _: "/test/xdg-open")
    reaped = threading.Event()
    def wait(timeout=None):
        if timeout is not None: raise subprocess.TimeoutExpired("xdg-open", timeout)
        reaped.set()
        return 0
    monkeypatch.setattr(D.subprocess, "Popen", Mock(return_value=Mock(wait=wait)))
    D.open_folder(tmp_path)
    assert reaped.wait(timeout=2)
