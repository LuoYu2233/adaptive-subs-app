"""Request the local desktop to open a folder without invoking a command shell."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import threading


class FolderOpenError(RuntimeError):
    pass


def open_folder(folder: Path):
    folder = folder.resolve()
    if not folder.is_dir():
        raise FolderOpenError("文件夹不存在，请确认程序已完整解压")
    if sys.platform == "win32":
        os.startfile(str(folder))
        return
    if sys.platform == "darwin":
        command = shutil.which("open")
    else:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise FolderOpenError("当前运行环境没有桌面，可以复制路径后手动打开")
        command = shutil.which("xdg-open")
    if not command:
        raise FolderOpenError("没有找到系统文件管理器，可以复制路径后手动打开")
    process = subprocess.Popen([command, str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        code = process.wait(timeout=.35)
    except subprocess.TimeoutExpired:
        # Some desktop file managers keep the launcher alive until their window closes.
        threading.Thread(target=process.wait, daemon=True).start()
        return
    if code != 0:
        raise FolderOpenError("系统未能打开文件夹，可以复制路径后手动打开")
