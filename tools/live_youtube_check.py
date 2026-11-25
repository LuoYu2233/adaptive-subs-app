"""Explicit live YouTube extension check, using a temporary profile and no model credentials.

python tools/live_youtube_check.py --video VIDEO_ID
Contacts the public video site and downloads its English subtitles. No account is used,
and the generation button is checked only for the missing-key guard. Not run by CI/cron.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def browser_proxy():
    address = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
    if not address:
        return {}
    url = urllib.parse.urlsplit(address)
    proxy = {"server": f"{url.scheme}://{url.hostname}:{url.port or 80}", "bypass": "127.0.0.1,localhost"}
    if url.username:
        proxy.update(username=urllib.parse.unquote(url.username), password=urllib.parse.unquote(url.password or ""))
    return {"proxy": proxy}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="public YouTube video ID with English subtitles")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", args.video):
        parser.error("video must be an 11-character YouTube ID")
    from playwright.sync_api import sync_playwright, expect

    output = ROOT / "test-results/live-platform"
    output.mkdir(parents=True, exist_ok=True)
    report = {"url": f"https://www.youtube.com/watch?v={args.video}", "timestamp": datetime.now(timezone.utc).isoformat(),
              "scope": "Live anonymous page, real installed extension and local server; no model generation"}
    with tempfile.TemporaryDirectory(prefix="asubs-live-youtube-") as temp:
        home = Path(temp)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env.update(ASUBS_HOME=temp, ASUBS_NO_BROWSER="1", ASUBS_HEADLESS="1", ANNOTATION_PROVIDER="deepseek")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (output / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-u", "app/server.py", "--port", str(port)],
                                      cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Local server exited")
                    try:
                        with opener.open(base + "/api/health", timeout=1) as response:
                            if json.load(response)["key"]:
                                raise RuntimeError("Live check must not have an API key")
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Local server did not become ready")
                with sync_playwright() as p:
                    extension = ROOT / "extension"
                    context = p.chromium.launch_persistent_context(str(home / "chromium"), channel="chromium", headless=True,
                        viewport={"width": 1280, "height": 900}, args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"], **browser_proxy())
                    page = context.new_page()
                    try:
                        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                        worker.evaluate("base => chrome.storage.session.set({base})", base)
                        worker.evaluate("() => chrome.storage.local.set({asubsKeysHint: 1})")
                        extension_errors = []
                        page.on("pageerror", lambda error: extension_errors.append(str(error)[:250]) if "chrome-extension://" in (error.stack or "") else None)
                        response = page.goto(report["url"], wait_until="domcontentloaded", timeout=45000)
                        report["http_status"] = response.status if response else None
                        for label in ("Reject all", "全部拒绝"):
                            button = page.get_by_role("button", name=label, exact=True)
                            if button.count():
                                button.first.click(timeout=5000)
                        pill = page.locator(".asubs-pill")
                        expect(pill).to_be_attached(timeout=30000)
                        page.wait_for_function("() => { const t = document.querySelector('.asubs-pill')?.textContent; return t && !t.includes('正在获取') && !t.includes('正在连接'); }", timeout=60000)
                        report.update(title=page.title(), subtitle_status=pill.inner_text(),
                                      extension_roots=page.locator(".asubs-root").count(), extension_errors=extension_errors)
                        if "生成注释" not in report["subtitle_status"]:
                            raise RuntimeError("字幕获取未通过：" + report["subtitle_status"][:200])
                        expect(page.locator("#movie_player video").first).to_be_attached()
                        expect(page.locator(".asubs-root")).to_have_count(1)
                        report.update(title=page.title(), subtitle_status=pill.inner_text(), extension_errors=extension_errors)
                        page.screenshot(path=str(output / "youtube-loaded.png"))
                        pill.get_by_role("button", name="生成注释", exact=True).click()
                        expect(pill).to_contain_text("API key", timeout=15000)
                        report["missing_key_guard"] = True
                        page.screenshot(path=str(output / "youtube-key-guard.png"))
                        request = urllib.request.Request(base + "/api/status", headers={"X-Adaptive-Subs": "1"})
                        with opener.open(request, timeout=10) as response:
                            status = json.load(response)
                        if status["jobs"] or (home / ".cache/usage.jsonl").exists() or extension_errors:
                            raise RuntimeError("Unexpected generation activity or extension error")
                        report["passed"] = True
                    except Exception:
                        try:
                            page.screenshot(path=str(output / "youtube-failure.png"))
                        except Exception:
                            pass
                        raise
                    finally:
                        context.close()
            except Exception as error:
                report.update(passed=False, error_type=type(error).__name__, error=str(error)[:500])
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    path = output / "youtube.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
