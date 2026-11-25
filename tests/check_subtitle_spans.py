"""Exercise Python note positions through both browser clients; all video/services are fixtures."""
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:19876"
WEB = "http://player.test"
VID = "abcdefghijk"


def check():
    text = "😀𝄞 bank and 🪙 banks."
    start = text.index("bank")
    data = {"video": {"id": VID, "title": "测试", "duration": 10}, "cues": [
        {"s": 0, "e": 10, "text": text, "zh": "", "notes": [
            {"s": start, "e": start+4, "key": "bank", "lv": 15000, "zh": "河岸", "kind": "sense"}]}]}
    viewer = {"level": 5000, "known": [], "unknown": ["bank"], "rev": 1, "marks": {"known": [], "unknown": ["bank"]}}
    with TemporaryDirectory() as user_data, sync_playwright() as pw:
        ext = ROOT / "extension"
        ctx = pw.chromium.launch_persistent_context(user_data, channel="chromium", headless=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        try:
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            worker.evaluate("base => chrome.storage.session.set({base})", BASE)
            worker.evaluate("() => chrome.storage.local.set({asubsDefs:{bank:'银行'}, asubsKeysHint:1})")

            def route(r):
                u = r.request.url
                if u == BASE + "/api/health": return r.fulfill(json={"ok": True, "profile": "/profile", "ext": 2})
                if u.startswith(BASE + "/api/profile"): return r.fulfill(json=viewer)
                if u == BASE + "/api/ext/open": return r.fulfill(json={"id": VID, "cached": True, "data": data})
                if u == "https://www.youtube.com/watch?v="+VID:
                    return r.fulfill(content_type="text/html",body='<div id="movie_player" style="height:400px"><video muted></video></div>')
                if u.startswith(WEB):
                    path = urlsplit(u).path.lstrip("/")
                    if path == "api/health": return r.fulfill(json={"ok": False})
                    if path == "data/index.json": return r.fulfill(json=[{"id": VID, "title": "测试", "file": VID+".json"}])
                    if path == "data/"+VID+".json": return r.fulfill(json=data)
                    if path == "player.html" or path.startswith("assets/"):
                        return r.fulfill(path=ROOT / "app/web" / path)
                return r.abort()

            ctx.route("**/*",route)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("if (location.hostname === 'player.test') localStorage.setItem('adaptive-subs:v2', JSON.stringify({introSeen:true}))")
            page.goto(WEB+"/player.html")
            row = page.locator(".tr-row")
            expect(row).to_contain_text("😀𝄞 bank(河岸) and 🪙 banks.")
            row.click()
            expect(page.locator("#line [data-n='0'] .w")).to_have_text("bank")
            page.locator("#line [data-n='0']").click()
            expect(page.locator(".wc-term")).to_have_text("bank")
            page.goto("https://www.youtube.com/watch?v="+VID)
            expect(page.locator(".asubs-w")).to_have_text(["bank", "banks"])
            page.locator(".asubs-t[data-n='0']").click()
            expect(page.locator(".asubs-card-word")).to_have_text("bank")
            assert not errors, errors
            print("Passed both clients: subtitle, transcript, word card and explicit local mark after supplementary characters.")
        finally:
            ctx.close()


if __name__ == "__main__":
    check()
