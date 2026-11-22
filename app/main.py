"""Entry point of the Windows program (AdaptiveSubs.exe, built by packaging/build.py; start.bat runs it from source).

A windowed program (no console): it starts the local server on this computer only and shows the
control panel in its own window (Edge WebView2). Until an API key is set, the panel points the user to
the settings page, where they pick the annotation model and enter their own keys. Everything that used to be
printed to the console goes to %LOCALAPPDATA%\\AdaptiveSubs\\logs\\adaptive-subs.log (and the panel's log).
Closing the window stops the program.
"""
import os
import sys
import traceback
from pathlib import Path


def home() -> Path:
    """Where settings and logs live: %LOCALAPPDATA%/AdaptiveSubs for the program, the repository root from source."""
    if os.environ.get("ASUBS_HOME"):
        return Path(os.environ["ASUBS_HOME"])
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AdaptiveSubs"
    return Path(__file__).resolve().parent.parent


def main():
    import applog
    applog.install(home() / "logs")
    print("Adaptive Subs —— 只给你不认识的词配中文的英语字幕")
    import server
    sys.argv = [sys.argv[0], "--app", "--gui", *sys.argv[1:]]
    server.main()


def show_error(text: str):
    """No console to print to: show the error in a message box."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text[-1500:], "Adaptive Subs 出错了", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        err = traceback.format_exc()
        try:
            print(err)
        finally:
            show_error("程序出错了，错误信息已写入日志：\n"
                       f"{home() / 'logs' / 'adaptive-subs.log'}\n\n{err}")
