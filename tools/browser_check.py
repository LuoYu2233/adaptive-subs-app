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
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY", "OPENAI_MODEL", "DEEPSEEK_MODEL", "MIMO_MODEL", "ASR_MODEL")}
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

                    calls = []
                    on_request = lambda r: calls.append(r.url) if "/api/" in r.url else None
                    page.on("request", on_request)
                    page.goto(base + "/demo")
                    expect(page.locator("#density")).to_contain_text("2 处提示")
                    page.locator('[data-note="0"]').click()
                    expect(page.locator("#meaningZh")).to_have_text("银行")
                    page.locator("#known").click()
                    expect(page.locator('[data-note="1"] .gloss')).to_have_text("河岸")
                    page.locator("#unknown").click()
                    page.locator("#level").focus()
                    page.locator("#level").press("End")
                    expect(page.locator('[data-note="0"] .gloss')).to_have_text("银行")
                    expect(page.locator('[data-note="1"] .gloss')).to_have_count(0)
                    page.get_by_text("中英对照", exact=True).click()
                    expect(page.locator(".translation")).to_have_count(3)
                    page.locator("#reset").click()
                    expect(page.locator("#density")).to_contain_text("2 处提示")
                    page.screenshot(path=str(output / "demo.png"), full_page=True)
                    page.remove_listener("request", on_request)
                    assert calls == [], f"Demo unexpectedly requested an API: {calls}"

                    page.goto(base + "/setup?app=1")
                    expect(page.locator("#key-deepseek")).to_be_visible()
                    page.locator("#model-deepseek").locator("..").locator("summary").click()
                    page.locator("#model-deepseek").fill("deepseek-custom-test")
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("还有设置需要检查")
                    page.reload()
                    expect(page.locator("#model-deepseek")).to_have_value("deepseek-custom-test")

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

                    # Original test sentences exercise the actual player and profile API;
                    # no YouTube content or model output is downloaded.
                    data = home / "data"
                    data.mkdir(exist_ok=True)
                    cues = []
                    for i, (text, zh) in enumerate((("The bank approved my loan.", "银行"),
                                                    ("We walked along the river bank.", "河岸"))):
                        offset = text.index("bank")
                        cues.append({"s": i * 5, "e": i * 5 + 5, "text": text, "zh": "",
                                     "notes": [{"s": offset, "e": offset + 4, "key": "bank", "zh": zh,
                                                "kind": "sense", "lv": 15000}]})
                    (data / "zzzzzzzzzzz.json").write_text(json.dumps({"status": "complete",
                        "video": {"id": "zzzzzzzzzzz", "title": "词义回归示例", "duration": 10}, "cues": cues}), encoding="utf-8")
                    (data / "index.json").write_text(json.dumps([{"id": "zzzzzzzzzzz", "title": "词义回归示例",
                                                                  "file": "zzzzzzzzzzz.json"}]), encoding="utf-8")
                    page.goto(base + "/player.html")
                    expect(page.locator("#tab-transcript .tr-row")).to_have_count(2)
                    page.locator("#introDialog").evaluate("element => { if (element.open) element.close(); }")
                    page.locator(".tabs [data-tab='preview']").click()
                    expect(page.locator("#levelValue")).to_contain_text("4,500")
                    expect(page.locator("#tab-preview .wrow")).to_have_count(2)
                    for meaning in ("银行", "河岸"):
                        row = page.locator("#tab-preview .wrow").filter(has=page.get_by_text(meaning, exact=True))
                        with page.expect_response(lambda r: r.url.endswith("/api/mark") and r.request.method == "POST") as response:
                            row.locator("[data-act='book']").click()
                        assert response.value.status == 200
                    expect(page.locator("#bookCount")).to_have_text("2")
                    row = page.locator("#tab-preview .wrow").filter(has=page.get_by_text("银行", exact=True))
                    with page.expect_response(lambda r: r.url.endswith("/api/mark") and r.request.method == "POST") as response:
                        row.locator("[data-act='know']").click()
                    assert response.value.status == 200
                    expect(page.locator("#tab-preview .wrow")).to_have_count(1)
                    expect(page.locator("#tab-preview .w-zh")).to_have_text("河岸")
                    page.screenshot(path=str(output / "meaning-feedback.png"), full_page=True)
                    page.goto(base + "/profile")
                    expect(page.locator("#marks")).to_contain_text("bank · 银行")
                    expect(page.locator("#marks")).to_contain_text("bank · 河岸")

                    with page.expect_download() as download:
                        page.locator("#exportProfile").click()
                    backup_path = home / "browser-backup.json"
                    download.value.save_as(backup_path)
                    exported = json.loads(backup_path.read_text())
                    assert exported["format"] == "adaptive-subs-profile"
                    assert exported["profile"]["field"] == "计算机"
                    page.locator("#field").fill("设计")
                    page.locator("#saveProfile").click()
                    expect(page.locator("#msg1")).to_contain_text("已保存")
                    page.locator("#backupFile").set_input_files(str(backup_path))
                    expect(page.locator("#restorePreview")).to_be_visible()
                    expect(page.locator("#field")).to_have_value("设计")
                    page.locator("#restoreProfile").click()
                    expect(page.locator("#dataMessage")).to_contain_text("画像已恢复")
                    expect(page.locator("#field")).to_have_value("计算机")
                    expect(page.locator("#marks")).to_contain_text("bank · 河岸")
                    page.locator("#dataTools details summary").click()
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#clearProfile").click()
                    expect(page.locator("#dataMessage")).to_contain_text("画像和标记已清除")
                    expect(page.locator("#field")).to_have_value("")
                    expect(page.locator("#identity [aria-pressed='true']")).to_have_count(0)
                    expect(page.locator("#s4")).to_be_hidden()
                    page.locator("#backupFile").set_input_files(str(backup_path))
                    expect(page.locator("#restorePreview")).to_be_visible()
                    page.locator("#restoreProfile").click()
                    expect(page.locator("#dataMessage")).to_contain_text("画像已恢复")
                    expect(page.locator("#marks")).to_contain_text("bank · 河岸")

                    page.set_viewport_size({"width": 390, "height": 844})
                    for path in ("app", "setup", "profile", "demo", "privacy"):
                        page.goto(base + "/" + path)
                        page.wait_for_load_state("networkidle")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Overflow on {path}"
                        if path == "app":
                            assert page.locator("#stage").evaluate("element => { const a=element.getBoundingClientRect(); const b=element.querySelector('.btn').getBoundingClientRect(); return b.bottom <= a.bottom && b.top >= a.top; }")
                        page.screenshot(path=str(output / f"{path}-mobile.png"), full_page=True)
                    assert not errors, errors
                    browser.close()
                print("Browser checks passed: recovery, settings, profile persistence, separate meaning feedback/wordbook, mobile layout.")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
