"""Exercise the profile backup page against the real local HTTP handlers and temporary storage."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server
from profile_store import Store


def main():
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw, ExitStack() as stack:
        home = Path(temp) / "学习记录"
        store = Store(home / "profile.json")
        store.update({"identity": "undergrad", "exams": {"cet4": 560}, "field": "计算机"})
        store.mark("sense:bank", "known")
        stack.enter_context(patch.multiple(server, HOME=home, DATA=home / "data", PROFILE=store))
        stack.enter_context(patch.object(server.A, "load_ranks", return_value={"bank": 300, "shore": 400}))
        httpd = server.Server(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        stack.callback(httpd.server_close)
        stack.callback(thread.join, 5)
        stack.callback(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        browser = pw.chromium.launch()
        stack.callback(browser.close)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        h = {"X-Adaptive-Subs": "1"}
        assert page.request.get(base + "/api/profile/export").status == 403
        page.goto(base + "/profile")
        expect(page.locator("#field")).to_have_value("计算机")
        with page.expect_download() as info: page.locator("#exportProfile").click()
        source = Path(temp) / "第一次备份.json"
        info.value.save_as(source)
        backup = json.loads(source.read_text(encoding="utf-8"))
        assert backup["profile"]["known"] == ["sense:bank"] and "rev" not in backup["profile"]
        backup["profile"]["field"] = "医学"
        source.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8-sig")
        page.locator("#backupFile").set_input_files(source)
        expect(page.locator("#restoreSummary")).to_contain_text("认识 1 条")
        assert store.load()["field"] == "计算机"
        mark = page.request.post(base + "/api/mark", headers=h, data={"key": "during", "state": "unknown"})
        assert mark.ok
        page.locator("#restoreProfile").click()
        expect(page.locator("#backupMessage")).to_contain_text("重新预览")
        assert store.load()["unknown"] == ["during"]
        before = store.path.read_bytes()
        page.locator("#backupFile").set_input_files(source)
        page.locator("#restoreProfile").click()
        expect(page.locator("#backupMessage")).to_contain_text("已恢复画像")
        expect(page.locator("#field")).to_have_value("医学")
        originals = home / "profile-backups"
        assert before in [f.read_bytes() for f in originals.iterdir()]
        assert store.export_backup() == backup

        damaged = b'{"not_complete":'
        store.path.write_bytes(damaged)
        page.goto(base + "/app")
        expect(page.locator("#d3")).to_contain_text("画像暂时无法读取")
        page.goto(base + "/profile")
        expect(page.locator("#profileLoadError")).to_contain_text("无法读取")
        expect(page.locator("#saveProfile")).to_be_hidden()
        assert page.request.post(base + "/api/mark", headers=h, data={"key": "late", "state": "known"}).status == 409
        assert store.path.read_bytes() == damaged
        page.locator("#backupFile").set_input_files(source)
        page.locator("#restoreProfile").click()
        expect(page.locator("#field")).to_have_value("医学")
        expect(page.locator("#profileLoadError")).to_be_hidden()
        assert damaged in [f.read_bytes() for f in originals.iterdir()]
        assert not errors, errors
        print("Passed the backup download, UTF-8 restore, concurrent mark conflict and damaged-profile recovery.")


if __name__ == "__main__":
    main()
