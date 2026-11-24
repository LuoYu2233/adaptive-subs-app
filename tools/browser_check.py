"""Exercise the real local server in Chromium, with temporary data and no provider keys.

Install requirements.txt and playwright, then run `python -m playwright install chromium`.
Screenshots and the server log go to ignored test-results/browser/.
No video download or model generation is requested by this check.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from task_store import TaskStore


def main():
    from playwright.sync_api import sync_playwright, expect

    output = ROOT / "test-results" / "browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adaptive-subs-browser-") as temp:
        home = Path(temp)
        TaskStore(home / "tasks.sqlite3").save("abcdefghijk", "running", {
            "version": 1, "title": "断点恢复示例", "started": time.time(), "done": 1, "total": 2,
            "provider": "deepseek", "model": "test", "ann": {}, "zh": {}, "cues": [],
        })
        env = {k: v for k, v in os.environ.items() if k not in (
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY", "OPENAI_MODEL")}
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
                        raise RuntimeError("Server exited; see test-results/browser/server.log")
                    try:
                        with opener.open(base + "/api/health", timeout=1) as response:
                            assert not json.load(response)["key"]
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Server did not become ready")
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width": 1280, "height": 900})
                    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    page.goto(base + "/app")
                    expect(page.locator("[data-job='resume']")).to_be_visible(timeout=20000)
                    expect(page.locator("#videos")).to_contain_text("待恢复")
                    page.locator("[data-job='resume']").click()
                    expect(page.locator("#toast")).to_contain_text("API key")
                    page.screenshot(path=str(output / "control-panel.png"), full_page=True)

                    page.goto(base + "/setup?app=1")
                    expect(page.locator("#key-deepseek")).to_be_visible()
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("还有 key 没设置好")

                    page.goto(base + "/profile?app=1")
                    page.locator("#identity [data-id='undergrad']").click()
                    page.locator("#field").fill("计算机")
                    page.locator("#ex-cet4").fill("560")
                    page.locator("#saveProfile").click()
                    expect(page.locator("#msg1")).to_contain_text("已保存")
                    page.reload()
                    expect(page.locator("#field")).to_have_value("计算机")
                    expect(page.locator("#identity [data-id='undergrad']")).to_have_attribute("aria-pressed", "true")
                    saved = json.loads((home / "profile.json").read_text())
                    assert saved["exams"]["cet4"] == 560

                    page.goto(base + "/player.html")
                    expect(page.locator("#introDialog")).to_be_visible()
                    page.locator("#introDialog").evaluate("element => element.close()")
                    expect(page.locator("#overlaySubs")).to_be_visible()
                    expect(page.locator("#line")).to_contain_text("粘贴")
                    expect(page.locator("#settingsLink")).to_be_visible()
                    page.locator("#urlInput").fill("abcdefghijk")
                    page.locator("#urlForm").evaluate("element => element.requestSubmit()")
                    expect(page.locator("#toast")).to_contain_text("API key")
                    page.screenshot(path=str(output / "player.png"), full_page=True)

                    page.set_viewport_size({"width": 390, "height": 844})
                    for path in ("app", "setup", "profile"):
                        page.goto(base + "/" + path)
                        page.wait_for_load_state("networkidle")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Overflow on {path}"
                        if path == "app":
                            assert page.locator("#stage").evaluate("element => { const a=element.getBoundingClientRect(); const b=element.querySelector('.btn').getBoundingClientRect(); return b.bottom <= a.bottom && b.top >= a.top; }")
                        page.screenshot(path=str(output / f"{path}-mobile.png"), full_page=True)
                    assert not errors, errors
                    browser.close()
                print("Browser checks passed: recovery UI, settings, persisted profile, missing-key handling, mobile layout.")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
