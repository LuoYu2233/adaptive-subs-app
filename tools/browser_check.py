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
        damaged_tasks = b"damaged original task database"
        (home / "tasks.sqlite3").write_bytes(damaged_tasks)
        recent_folder = home / "data"
        recent_folder.mkdir()
        recent_fixtures = []
        for ident, title, state in (("badbadbad00", "\ud800", "complete"),
                                  ("partial0000", "不应作为完成记录显示", "partial"),
                                  ("complete000", "最近视频的原创长标题" * 100, "complete")):
            path = recent_folder / f"{ident}.json"
            path.write_text(json.dumps({"video": {"id": ident, "title": title, "duration": 1}, "status": state,
                "cues": [{"s": 0, "e": 1, "text": "Original status sample.", "notes": []}]}))
            recent_fixtures.append(path)
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
                    expect(page.locator("#taskIssue")).to_be_visible(timeout=20000)
                    expect(page.locator("#videos")).to_contain_text("最近视频的原创长标题")
                    expect(page.locator("#videos")).not_to_contain_text("不应作为完成记录显示")
                    for path in recent_fixtures: path.unlink()
                    assert (home / "tasks.sqlite3").read_bytes() == damaged_tasks
                    page.screenshot(path=str(output / "task-recovery.png"), full_page=True)
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#recoverTasks").click()
                    expect(page.locator("#taskIssue")).to_be_hidden()
                    assert next((home / "recovery").glob("tasks-*")).read_bytes() == damaged_tasks
                    TaskStore(home / "tasks.sqlite3").save("abcdefghijk", "interrupted", {
                        "version": 1, "title": "断点恢复示例", "started": time.time(), "done": 1, "total": 2,
                        "provider": "deepseek", "model": "test", "ann": {}, "zh": {}, "cues": [],
                    })
                    page.reload()
                    expect(page.locator("[data-job='resume']")).to_be_visible(timeout=20000)
                    expect(page.locator("#videos")).to_contain_text("待恢复")
                    page.locator("[data-job='resume']").click()
                    expect(page.locator("#toast")).to_contain_text("API key")
                    TaskStore(home / "tasks.sqlite3").save("yt_legacy00000_asr", "interrupted", {
                        "title": "旧语音任务示例", "asr_url": "https://www.youtube.com/watch?v=legacy00000",
                        "started": time.time(), "done": 0, "total": 1})
                    page.reload()
                    expect(page.locator("#videos")).to_contain_text("可能产生新的语音用量")
                    page.screenshot(path=str(output / "control-panel.png"), full_page=True)

                    folder_mode = "unavailable"
                    folder_path = str(home / "数据与学习 notes")
                    def folder_response(route):
                        if folder_mode == "success": return route.fulfill(json={"ok": True})
                        if folder_mode == "bad": return route.fulfill(status=500, json={})
                        return route.fulfill(status=501, json={"error": "当前没有桌面，可以复制路径后手动打开", "path": folder_path})
                    page.route(base + "/api/open-folder", folder_response)
                    page.locator('[data-folder="data"]').click()
                    expect(page.locator("#folderDlg")).to_be_visible()
                    expect(page.locator("#folderPath")).to_have_value(folder_path)
                    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
                    page.locator("#copyFolder").click()
                    expect(page.locator("#folderMessage")).to_contain_text("路径已复制")
                    assert page.evaluate("navigator.clipboard.readText()") == folder_path
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.locator("#folderDlg").evaluate("element => element.getBoundingClientRect().left >= 0 && element.getBoundingClientRect().right <= innerWidth")
                    page.screenshot(path=str(output / "folder-fallback-mobile.png"), animations="disabled")
                    page.locator("#folderDlg [data-close]").click()
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.locator("#extHelp").click()
                    page.locator('#extDlg [data-folder="extension"]').click()
                    expect(page.locator("#folderDlg")).to_be_visible()
                    page.evaluate("""() => {
                        window.restoreClipboard = navigator.clipboard.writeText.bind(navigator.clipboard);
                        navigator.clipboard.writeText = () => Promise.reject(new Error('Synthetic clipboard failure'));
                    }""")
                    page.locator("#copyFolder").click()
                    expect(page.locator("#folderMessage")).to_contain_text("路径已选中")
                    assert page.locator("#folderPath").evaluate("input => input.selectionStart === 0 && input.selectionEnd === input.value.length")
                    page.evaluate("navigator.clipboard.writeText = window.restoreClipboard; delete window.restoreClipboard")
                    page.locator("#folderDlg [data-close]").click()
                    expect(page.locator("#extDlg")).to_be_visible()
                    page.locator("#extDlg [data-close]").click()
                    folder_mode = "success"
                    page.locator('[data-folder="data"]').click()
                    expect(page.locator("#toast")).to_contain_text("已请求系统打开文件夹")
                    folder_mode = "bad"
                    page.locator('[data-folder="data"]').click()
                    expect(page.locator("#toast")).to_contain_text("系统未确认打开请求")
                    page.unroute(base + "/api/open-folder")

                    # Never claim the process stopped when a quit request failed or it still responds.
                    quit_mode = "rejected"
                    quit_requests = []
                    def quit_response(route):
                        quit_requests.append(route.request.method)
                        if quit_mode == "network": return route.abort()
                        if quit_mode == "rejected": return route.fulfill(status=503, json={"error": "测试退出失败"})
                        return route.fulfill(json={"ok": True})
                    page.route(base + "/api/quit", quit_response)
                    page.locator("#quit").click()
                    page.locator("#quitYes").click()
                    expect(page.locator("#quitError")).to_contain_text("测试退出失败")
                    expect(page.locator("#live")).to_be_visible()
                    expect(page.locator("#quitYes")).to_be_enabled()
                    quit_mode = "network"
                    page.locator("#quitYes").click()
                    expect(page.locator("#quitError")).to_contain_text("退出结果未确认")
                    quit_mode = "acknowledged"
                    page.locator("#quitYes").click()
                    expect(page.locator("#quitYes")).to_be_disabled()
                    expect(page.locator("#quitError")).to_contain_text("程序仍在响应", timeout=15000)
                    assert len(quit_requests) == 3
                    page.screenshot(path=str(output / "quit-unconfirmed.png"), full_page=True)
                    # Hold an in-flight status read to verify it cannot redraw a disconnected console.
                    delayed_status = []
                    page.route(base + "/api/status", lambda route: delayed_status.append(route))
                    for _ in range(100):
                        if delayed_status: break
                        page.wait_for_timeout(30)
                    assert delayed_status
                    page.route(base + "/api/health", lambda route: route.abort())
                    page.locator("#quitYes").click()
                    expect(page.get_by_text("已与程序断开连接", exact=True)).to_be_visible()
                    for route in delayed_status: route.fulfill(json={"late": "discard this response"})
                    page.wait_for_timeout(100)
                    assert len(quit_requests) == 4 and server.poll() is None
                    page.unroute(base + "/api/quit")
                    page.unroute(base + "/api/status")
                    page.unroute(base + "/api/health")

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

                    # Failure paths use the real settings page and protected local API.
                    settings_mode = "load-error"
                    settings_calls = {"save": 0, "check": 0}
                    pending_checks = []
                    def settings_response(route):
                        if route.request.method == "GET":
                            if settings_mode == "load-error": return route.fulfill(status=503, json={})
                            return route.continue_()
                        settings_calls["save"] += 1
                        if settings_mode == "save-error": return route.fulfill(status=500, json={})
                        if settings_mode == "lost-save":
                            route.fetch()  # The real write succeeds, then its response is lost.
                            return route.abort("failed")
                        return route.continue_()
                    def settings_check_response(route):
                        settings_calls["check"] += 1
                        if settings_mode == "check-error": return route.fulfill(status=502, json={})
                        if settings_mode == "check-timeout":
                            pending_checks.append(route)
                            return
                        if settings_mode == "check-success":
                            return route.fulfill(json={"provider": "deepseek", "checked_models": {
                                "deepseek": "confirmed-model", "asr": "confirmed-asr"},
                                "results": {"deepseek": "ok", "asr": "ok"}})
                        return route.continue_()
                    page.route(base + "/api/settings", settings_response)
                    page.route(base + "/api/settings/test", settings_check_response)
                    page.goto(base + "/setup?app=1")
                    expect(page.locator("#msg")).to_contain_text("读取设置失败")
                    expect(page.locator("#save")).to_be_disabled()
                    expect(page.locator("#reloadSettings")).to_be_enabled()
                    settings_mode = "save-error"
                    page.locator("#reloadSettings").click()
                    expect(page.locator("#save")).to_be_enabled()
                    page.locator("#model-deepseek").locator("..").locator("summary").click()
                    page.locator("#model-deepseek").fill("persisted-after-lost-response")
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("未确认设置是否保存")
                    assert settings_calls == {"save": 1, "check": 0}
                    expect(page.locator("#save")).to_be_enabled()
                    settings_mode = "lost-save"
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("没有自动重试")
                    assert settings_calls == {"save": 2, "check": 0}
                    page.locator("#reloadSettings").click()
                    expect(page.locator("#model-deepseek")).to_have_value("persisted-after-lost-response")
                    settings_mode = "check-error"
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("设置已保存；后续检查未完成")
                    assert settings_calls == {"save": 3, "check": 1}
                    settings_mode = "check-timeout"
                    page.evaluate("""() => {
                        window.settingsOriginalTimer = window.setTimeout;
                        window.setTimeout = (callback, delay, ...args) => settingsOriginalTimer(callback, delay === 40000 ? 50 : delay, ...args);
                    }""")
                    page.locator("#checkSaved").click()
                    expect(page.locator("#msg")).to_contain_text("请求超时")
                    expect(page.locator("#msg")).to_contain_text("已保存的设置保持不变")
                    page.evaluate("window.setTimeout = window.settingsOriginalTimer; delete window.settingsOriginalTimer")
                    for route in pending_checks: route.abort("failed")
                    settings_mode = "check-success"
                    page.locator("#checkSaved").click()
                    expect(page.locator("#msg")).to_contain_text("实际生成能力和额度需以使用结果为准")
                    expect(page.locator("#results")).to_contain_text("confirmed-model")
                    assert settings_calls == {"save": 3, "check": 3}
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(output / "settings-recovered-mobile.png"), full_page=True)
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.unroute(base + "/api/settings", settings_response)
                    page.unroute(base + "/api/settings/test", settings_check_response)

                    page.goto(base + "/setup?app=1")
                    expect(page.locator("#key-deepseek")).to_be_visible()
                    page.locator("#model-deepseek").locator("..").locator("summary").click()
                    page.locator("#model-deepseek").fill("deepseek-custom-test")
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("还有设置需要检查")
                    page.reload()
                    expect(page.locator("#model-deepseek")).to_have_value("deepseek-custom-test")

                    second_settings = context.new_page()
                    second_settings.on("pageerror", lambda error: errors.append(str(error)))
                    second_settings.goto(base + "/setup")
                    expect(second_settings.locator("#save")).to_be_enabled()
                    stale_checks = []
                    second_settings.on("request", lambda request: stale_checks.append(request.url) if request.url.endswith("/api/settings/test") else None)
                    page.locator("#model-deepseek").locator("..").locator("summary").click()
                    page.locator("#model-deepseek").fill("model-from-first-window")
                    page.locator("#save").click()
                    expect(page.locator("#msg")).to_contain_text("还有设置需要检查")
                    second_settings.locator("#model-deepseek").locator("..").locator("summary").click()
                    second_settings.locator("#model-deepseek").fill("model-from-stale-window")
                    second_settings.locator("#save").click()
                    expect(second_settings.locator("#msg")).to_contain_text("当前修改没有写入")
                    expect(second_settings.locator("#save")).to_be_disabled()
                    expect(second_settings.locator("#model-deepseek")).to_have_value("model-from-stale-window")
                    assert stale_checks == []
                    second_settings.screenshot(path=str(output / "settings-conflict.png"), full_page=True)
                    second_settings.locator("#reloadSettings").click()
                    expect(second_settings.locator("#model-deepseek")).to_have_value("model-from-first-window")
                    expect(second_settings.locator("#save")).to_be_enabled()
                    second_settings.locator("#model-deepseek").locator("..").locator("summary").click()
                    second_settings.locator("#model-deepseek").fill("confirmed-after-rereading")
                    second_settings.locator("#save").click()
                    expect(second_settings.locator("#msg")).to_contain_text("还有设置需要检查")
                    page.reload()
                    expect(page.locator("#model-deepseek")).to_have_value("confirmed-after-rereading")
                    second_settings.close()

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
                    for i, (text, zh) in enumerate((("😀 The bank approved my loan.", "银行"),
                                                    ("We walked along the river bank.", "河岸"))):
                        offset = text.index("bank")
                        cues.append({"s": i * 5, "e": i * 5 + 5, "text": text, "zh": "",
                                     "notes": [{"s": offset, "e": offset + 4, "key": "bank", "zh": zh,
                                                "kind": "sense", "lv": 15000}]})
                    (data / "zzzzzzzzzzz.json").write_text(json.dumps({"status": "complete",
                        "video": {"id": "zzzzzzzzzzz", "title": "词义回归示例", "duration": 10}, "cues": cues}), encoding="utf-8")
                    (data / "index.json").write_text(json.dumps([{"id": "zzzzzzzzzzz", "title": "词义回归示例",
                                                                  "file": "zzzzzzzzzzz.json"}]), encoding="utf-8")
                    # The library is recoverable from completed subtitles; reading never overwrites the broken index.
                    original_index = (data / "index.json").read_bytes()
                    for damaged_index in (b'{broken-library', b'[{"id":"old","title":"Old","file":"old.json"},"broken-row"]'):
                        (data / "index.json").write_bytes(damaged_index)
                        page.goto(base + "/player.html")
                        expect(page.locator("#tab-transcript .tr-row")).to_have_count(2)
                        expect(page.locator("#videoSelect")).to_contain_text("词义回归示例")
                        assert (data / "index.json").read_bytes() == damaged_index
                    (data / "index.json").write_bytes(original_index)
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
                    # Wordbook backups are lossless, previewed and merged without changing profile marks.
                    page.locator(".tabs [data-tab='book']").click()
                    with page.expect_download() as download:
                        page.locator('[data-act="backup-book"]').click()
                    book_path = home / "wordbook-backup.json"
                    download.value.save_as(book_path)
                    book_backup = json.loads(book_path.read_text())
                    assert book_backup["format"] == "adaptive-subs-wordbook"
                    assert {item["zh"] for item in book_backup["entries"]} == {"银行", "河岸"}
                    assert "😀" in book_backup["entries"][0]["sentence"]
                    profile_before = (home / "profile.json").read_bytes()

                    def fail_browser_storage():
                        page.evaluate("""() => {
                          window.originalSetItem = Storage.prototype.setItem;
                          Storage.prototype.setItem = function(key, value) {
                            if (key === 'adaptive-subs:v2') throw new DOMException('Test quota', 'QuotaExceededError');
                            return window.originalSetItem.call(this, key, value);
                          };
                        }""")

                    def restore_browser_storage():
                        page.evaluate("() => { Storage.prototype.setItem = window.originalSetItem; }")

                    fail_browser_storage()
                    page.locator('[data-act="unbook"]').first.click()
                    expect(page.locator("#toast")).to_contain_text("浏览器存储不可用")
                    expect(page.locator("#bookCount")).to_have_text("2")
                    restore_browser_storage()
                    page.locator('[data-act="unbook"]').first.click()
                    expect(page.locator("#bookCount")).to_have_text("1")
                    fail_browser_storage()
                    page.locator("#toast button").click()
                    expect(page.locator("#toast")).to_be_visible()
                    expect(page.locator("#toast")).to_contain_text("浏览器存储不可用")
                    expect(page.locator("#bookCount")).to_have_text("1")
                    restore_browser_storage()
                    assert len(page.evaluate("JSON.parse(localStorage.getItem('adaptive-subs:v2')).book")) == 1
                    page.locator("#bookBackupFile").set_input_files(str(book_path))
                    expect(page.locator("#bookImportSummary")).to_contain_text("将新增 1 条")
                    expect(page.locator("#bookCount")).to_have_text("1")
                    page.locator("#bookImportCancel").click()
                    expect(page.locator("#bookCount")).to_have_text("1")
                    page.locator("#bookBackupFile").set_input_files(str(book_path))
                    expect(page.locator("#bookImportDialog")).to_be_visible()
                    fail_browser_storage()
                    page.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookImportError")).to_contain_text("原有生词本未改变")
                    expect(page.locator("#bookCount")).to_have_text("1")
                    restore_browser_storage()
                    page.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookImportDialog")).not_to_be_visible()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    page.locator("#bookBackupFile").set_input_files(str(book_path))
                    expect(page.locator("#bookImportSummary")).to_contain_text("将新增 0 条")
                    page.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    invalid_book = home / "wordbook-invalid.json"
                    invalid_book.write_text(json.dumps({**book_backup, "entries": [book_backup["entries"][0], {"key": "broken"}]}))
                    page.locator("#bookBackupFile").set_input_files(str(invalid_book))
                    expect(page.locator("#toast")).to_contain_text("无法读取生词本备份")
                    expect(page.locator("#bookCount")).to_have_text("2")
                    assert (home / "profile.json").read_bytes() == profile_before
                    page.reload()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    expect(page.locator("#tab-book .wrow")).to_have_count(2)
                    page.screenshot(path=str(output / "wordbook-backup.png"), full_page=True)
                    # Two real tabs share records. A display setting must not resurrect a deleted entry.
                    other = context.new_page()
                    other.on("pageerror", lambda error: errors.append(str(error)))
                    other.goto(base + "/player.html")
                    expect(other.locator("#bookCount")).to_have_text("2")
                    page.locator('[data-act="unbook"]').first.click()
                    expect(page.locator("#bookCount")).to_have_text("1")
                    expect(other.locator("#bookCount")).to_have_text("1")
                    other.locator('[data-mode="plain"]').click()
                    expect(other.locator('[data-mode="plain"]')).to_have_attribute("aria-checked", "true")
                    assert len(page.evaluate("JSON.parse(localStorage.getItem('adaptive-subs:v2')).book")) == 1
                    page.locator("#toast button").click()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    expect(other.locator("#bookCount")).to_have_text("2")

                    # Hold the actual Web Lock, queue two imports, then release it to force contention.
                    for index, target in enumerate((page, other)):
                        imported = {**book_backup, "entries": [*book_backup["entries"],
                            {**book_backup["entries"][0], "key": f"concurrent-{index}", "term": f"entry{index}"}]}
                        target.locator("#bookBackupFile").set_input_files({"name": "merge.json", "mimeType": "application/json",
                            "buffer": json.dumps(imported).encode()})
                        expect(target.locator("#bookImportDialog")).to_be_visible()
                    page.evaluate("""() => {
                      window.lockHeld = false;
                      navigator.locks.request('adaptive-subs:v2', async () => {
                        window.lockHeld = true;
                        await new Promise(resolve => { window.releaseStorageLock = resolve; });
                      });
                    }""")
                    page.wait_for_function("() => window.lockHeld")
                    page.locator("#bookImportConfirm").click()
                    other.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    page.evaluate("() => window.releaseStorageLock()")
                    expect(page.locator("#bookCount")).to_have_text("4")
                    expect(other.locator("#bookCount")).to_have_text("4")
                    expect(page.locator("#bookImportDialog")).not_to_be_visible()
                    expect(other.locator("#bookImportDialog")).not_to_be_visible()
                    other.close()

                    # Corrupt local state cannot crash startup or be overwritten by unrelated controls.
                    damaged_store = '{"book":["preserve-original",'
                    page.evaluate("raw => localStorage.setItem('adaptive-subs:v2', raw)", damaged_store)
                    page.reload()
                    expect(page.locator("#storageNotice")).to_be_visible()
                    expect(page.locator("#introDialog")).not_to_be_visible()
                    page.locator('[data-mode="plain"]').click()
                    expect(page.locator("#toast")).to_contain_text("本地数据损坏")
                    assert page.evaluate("localStorage.getItem('adaptive-subs:v2')") == damaged_store
                    page.locator(".tabs [data-tab='book']").click()
                    page.locator('[data-act="backup-book"]').click()
                    expect(page.locator("#toast")).to_contain_text("备份失败")
                    with page.expect_download() as download:
                        page.locator("#downloadStorage").click()
                    original_path = home / "player-original.json"
                    download.value.save_as(original_path)
                    assert json.loads(original_path.read_text())["copies"][0]["raw"] == damaged_store
                    page.evaluate("""() => {
                      window.originalSetItem = Storage.prototype.setItem;
                      Storage.prototype.setItem = function(key, value) {
                        if (key.startsWith('adaptive-subs:v2:recovery:')) throw new DOMException('Test quota', 'QuotaExceededError');
                        return window.originalSetItem.call(this, key, value);
                      };
                    }""")
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#recoverStorage").click()
                    expect(page.locator("#toast")).to_contain_text("重建失败")
                    assert page.evaluate("localStorage.getItem('adaptive-subs:v2')") == damaged_store
                    restore_browser_storage()
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#recoverStorage").click()
                    expect(page.locator("#storageNotice")).to_be_hidden()
                    expect(page.locator("#storageRecovery")).to_be_visible()
                    expect(page.locator("#introDialog")).to_be_visible()
                    page.locator("#introSkip").click()
                    with page.expect_download() as download:
                        page.locator("#downloadRecovery").click()
                    download.value.save_as(original_path)
                    assert json.loads(original_path.read_text())["copies"][0]["raw"] == damaged_store
                    page.locator(".tabs [data-tab='book']").click()
                    expect(page.locator('[data-act="import-book"]')).to_be_visible()
                    page.locator("#bookBackupFile").set_input_files(str(book_path))
                    expect(page.locator("#bookImportSummary")).to_contain_text("将新增 2 条")
                    page.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookCount")).to_have_text("2")
                    assert (home / "profile.json").read_bytes() == profile_before
                    page.screenshot(path=str(output / "player-storage-recovery.png"), full_page=True)
                    page.locator(".tabs [data-tab='preview']").click()
                    row = page.locator("#tab-preview .wrow").filter(has=page.get_by_text("银行", exact=True))
                    page.route("**/api/mark", lambda route: route.fulfill(status=507, content_type="application/json", body=json.dumps({"error": "测试保存失败"})))
                    row.locator("[data-act='know']").click()
                    expect(page.locator("#toast")).to_contain_text("标记保存未确认")
                    expect(page.locator("#tab-preview .wrow")).to_have_count(2)
                    page.unroute("**/api/mark")
                    with page.expect_response(lambda r: r.url.endswith("/api/mark") and r.request.method == "POST") as response:
                        row.locator("[data-act='know']").click()
                    assert response.value.status == 200
                    expect(page.locator("#tab-preview .wrow")).to_have_count(1)
                    expect(page.locator("#tab-preview .w-zh")).to_have_text("河岸")
                    page.locator(".tabs [data-tab='transcript']").click()
                    expect(page.locator(".tr-row").first).not_to_contain_text("银行")
                    expect(page.locator(".tr-row").nth(1)).to_contain_text("河岸")
                    page.locator(".tabs [data-tab='preview']").click()
                    page.screenshot(path=str(output / "meaning-feedback.png"), full_page=True)
                    page.goto(base + "/profile")
                    expect(page.locator("#marks")).to_contain_text("bank · 银行")
                    expect(page.locator("#marks")).to_contain_text("bank · 河岸")

                    # A damaged saved file must offer an explicit retry, without starting work on read.
                    broken = home / "data/zzzzzzzzzzz.json"
                    complete = broken.read_text()
                    broken.write_text('{"video":{},"cues":["invalid"]}')
                    page.goto(base + "/player.html")
                    expect(page.locator('[data-generation="retry"]')).to_be_visible()
                    page.locator('.retry-btn').click()
                    expect(page.locator("#toast")).to_contain_text("API key")
                    page.locator('[data-generation="retry"]').click()
                    expect(page.locator("#toast")).to_contain_text("API key")
                    broken.write_text(complete)
                    page.goto(base + "/profile")

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

                    damaged_profile = b'{"known":["preserve-original",'
                    (home / "profile.json").write_bytes(damaged_profile)
                    page.goto(base + "/app")
                    expect(page.locator("#b3")).to_have_text("恢复画像")
                    page.goto(base + "/profile")
                    expect(page.locator("#recoveryPanel")).to_be_visible()
                    assert (home / "profile.json").read_bytes() == damaged_profile
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#rebuildProfile").click()
                    expect(page.locator("#dataMessage")).to_contain_text("原始文件已保留")
                    assert next((home / "recovery").glob("profile-*")).read_bytes() == damaged_profile
                    page.locator("#backupFile").set_input_files(str(backup_path))
                    expect(page.locator("#restorePreview")).to_be_visible()
                    page.locator("#restoreProfile").click()
                    expect(page.locator("#dataMessage")).to_contain_text("画像已恢复")
                    expect(page.locator("#marks")).to_contain_text("bank · 河岸")

                    # Thousands of saved words stay searchable without thousands of DOM rows.
                    page.goto(base + "/player.html?v=zzzzzzzzzzz")
                    page.locator(".tabs [data-tab='book']").click()
                    long_book = [{**book_backup["entries"][0], "key": f"benchmark-{i}", "term": f"term{i}",
                                  "zh": "测试词语", "sentence": f"Original example number {i}."} for i in range(5000)]
                    long_book[-1]["term"] = "x" * 500
                    page.locator("#bookBackupFile").set_input_files({"name": "large-book.json", "mimeType": "application/json",
                        "buffer": json.dumps({**book_backup, "entries": long_book}).encode()})
                    expect(page.locator("#bookImportSummary")).to_contain_text("将新增 5000 条")
                    page.locator("#bookImportConfirm").click()
                    expect(page.locator("#bookCount")).to_have_text("5002")
                    expect(page.locator("#bookList .wrow")).to_have_count(50)
                    expect(page.locator("#bookPageSummary")).to_contain_text("第 1 / 101 页")
                    book_timings = []
                    for mode in ("plain", "adaptive", "plain"):
                        begin = time.monotonic()
                        page.locator(f'#modeSeg [data-mode="{mode}"]').click()
                        page.wait_for_function("mode => JSON.parse(localStorage.getItem('adaptive-subs:v2')).mode === mode", arg=mode)
                        book_timings.append(round((time.monotonic() - begin) * 1000))
                    page.locator("#bookNext").click()
                    expect(page.locator("#bookPageSummary")).to_contain_text("第 2 / 101 页")
                    expect(page.locator("#bookList .w-term").first).to_have_text("term4949")
                    search = page.locator("#bookSearch").element_handle()
                    page.locator("#bookSearch").fill("ORIGINAL EXAMPLE NUMBER 1234.")
                    expect(page.locator("#bookList .wrow")).to_have_count(1)
                    expect(page.locator("#bookList .w-term")).to_have_text("term1234")
                    assert search.evaluate("e => e.isConnected && document.activeElement === e")
                    with page.expect_download() as download:
                        page.locator('[data-act="backup-book"]').click()
                    filtered_backup = home / "filtered-backup.json"
                    download.value.save_as(filtered_backup)
                    assert len(json.loads(filtered_backup.read_text())["entries"]) == 5002
                    page.locator('[data-act="unbook"]').click()
                    expect(page.locator("#bookList")).to_contain_text("没有匹配的记录")
                    page.locator("#toast button").click()
                    expect(page.locator("#bookList .wrow")).to_have_count(1)
                    page.locator("#bookSearch").fill("测试词语")
                    expect(page.locator("#bookPageSummary")).to_contain_text("找到 5000 条")
                    page.locator("#bookSearch").fill("不存在的搜索记录")
                    expect(page.locator("#bookList .wrow")).to_have_count(0)
                    page.locator('[data-act="book-clear"]').click()
                    expect(page.locator("#bookList .wrow")).to_have_count(50)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Long word overflow"
                    page.screenshot(path=str(output / "wordbook-search-mobile.png"), full_page=True)
                    page.set_viewport_size({"width": 1280, "height": 900})
                    (output / "wordbook-performance.json").write_text(json.dumps({"entries": 5002,
                        "rendered_rows": page.locator("#bookList .wrow").count(), "mode_switch_ms": book_timings}))
                    await_script = "entries => AdaptivePlayerStore.create().update(current => ({ ...current, book: entries }))"
                    page.evaluate(await_script, book_backup["entries"])

                    # Long transcripts keep unaffected DOM rows when mode/gloss state changes.
                    response = page.request.post(base + "/api/profile", data={"reset_marks": True}, headers={"X-Adaptive-Subs": "1"})
                    assert response.status == 200
                    long_cues = [{"s": i * 5, "e": i * 5 + 5, "text": f"The bank approved request number {i}.",
                                  "zh": "银行批准了申请。", "notes": [{"s": 4, "e": 8, "key": "bank", "zh": "银行",
                                  "kind": "word", "lv": 15000}]} for i in range(6000)]
                    (data / "yyyyyyyyyyy.json").write_text(json.dumps({"video": {"id": "yyyyyyyyyyy", "title": "长字幕回归", "duration": 30000}, "cues": long_cues}))
                    entries = json.loads((data / "index.json").read_text())
                    (data / "index.json").write_text(json.dumps(entries + [{"id": "yyyyyyyyyyy", "title": "长字幕回归", "file": "yyyyyyyyyyy.json"}]))
                    page.goto(base + "/player.html?v=yyyyyyyyyyy")
                    page.locator(".tabs [data-tab='transcript']").click()
                    expect(page.locator(".tr-row")).to_have_count(6000)
                    retained = page.locator(".tr-row").nth(3000).element_handle()
                    timings = []
                    for mode in ("plain", "adaptive", "plain", "adaptive"):
                        begin = time.monotonic()
                        page.locator(f'#modeSeg [data-mode="{mode}"]').click()
                        timings.append(round((time.monotonic() - begin) * 1000))
                        assert retained.evaluate("element => element.isConnected")
                    expect(page.locator(".tr-row").last).to_contain_text("5999")
                    (output / "transcript-performance.json").write_text(json.dumps({"cues": 6000, "mode_switch_ms": timings}))

                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(base + "/player.html?v=zzzzzzzzzzz")
                    page.locator(".tabs [data-tab='book']").click()
                    expect(page.locator('[data-act="import-book"]')).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Overflow on wordbook"
                    page.locator("#bookBackupFile").set_input_files(str(book_path))
                    expect(page.locator("#bookImportDialog")).to_be_visible()
                    assert page.locator("#bookImportDialog").evaluate("e => e.getBoundingClientRect().right <= innerWidth")
                    page.screenshot(path=str(output / "wordbook-mobile.png"), full_page=True)
                    page.locator("#bookImportCancel").click()
                    for path in ("app", "setup", "profile", "demo", "privacy"):
                        page.goto(base + "/" + path)
                        page.wait_for_load_state("networkidle")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Overflow on {path}"
                        if path == "app":
                            assert page.locator("#stage").evaluate("element => { const a=element.getBoundingClientRect(); const b=element.querySelector('.btn').getBoundingClientRect(); return b.bottom <= a.bottom && b.top >= a.top; }")
                        page.screenshot(path=str(output / f"{path}-mobile.png"), full_page=True)
                    assert not errors, errors
                    from player_check import check_player
                    check_player(context, base, output)
                    browser.close()
                    from extension_check import check_extension
                    check_extension(p, base, home, output)
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
