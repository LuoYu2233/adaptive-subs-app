"""Entry point of the packaged Windows program (AdaptiveSubs.exe, built by build_app.py).

Starts the local server on this computer only. On first run it opens the settings page,
where the user picks the annotation model and enters their own API keys.
Keep the window open while watching; closing it stops the program.
"""
import sys
import traceback


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)   # show each log line right away
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    print("Adaptive Subs —— 只给你不认识的词配中文的英语字幕")
    print("看视频时请保持这个窗口开着，关掉窗口就停止。\n")
    import server
    sys.argv = [sys.argv[0], "--app", *sys.argv[1:]]
    server.main()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        input("\n程序出错了（上面是错误信息）。按回车关闭窗口…")
