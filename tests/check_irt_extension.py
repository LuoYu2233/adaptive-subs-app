"""Exercise the measured model on existing subtitles in an installed Chromium extension."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
VID = "abcdefghijk"
BASE = "http://127.0.0.1:19876"


def check():
    terms = [("orbit", 1000), ("house", 15000), ("bank", 12000), ("fog", 1000)]
    sentence = " ".join(w for w, _ in terms)
    notes = [{"s": sentence.index(w), "e": sentence.index(w) + len(w), "key": w, "kind": "word", "zh": "释义", "lv": lv}
             for w, lv in terms]
    video = {"video": {"id": VID, "title": "测试", "duration": 4}, "cues": [
        {"s": 0, "e": 4, "text": sentence, "notes": notes, "zh": ""}]}
    profile = {"level": 5000, "known": ["orbit", "bank"], "unknown": ["fog"], "rev": 1,
               "marks": {"known": ["bank"], "unknown": ["fog"]}}
    table = {"version": 1, "scale": "natural-log lemma rank", "words": {
        w: {"b_initial": 6.2 if w in ("orbit","bank") else 3, "b_frequency": 7 if w == "house" else 3} for w, _ in terms}}
    with TemporaryDirectory() as directory, sync_playwright() as pw:
        folder = Path(directory)
        tab_file, param_file = folder / "难度.json", folder / "判词.json"
        tab_file.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        params = {"tableSha256": sha256(tab_file.read_bytes()).hexdigest(), "thetaByField": {"b_initial": 6, "b_frequency": 6},
                  "discrimination": 2}
        param_file.write_text(json.dumps(params), encoding="utf-8-sig")
        ext = ROOT / "extension"
        ctx = pw.chromium.launch_persistent_context(str(folder / "browser"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        events = []
        try:
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            origin = worker.url.rsplit("/", 1)[0]
            worker.evaluate("base => chrome.storage.session.set({base})", BASE)
            worker.evaluate("() => chrome.storage.local.set({settings:{pref:'normal'},asubsKeysHint:1})")

            def route(r):
                url = r.request.url
                if url.startswith("chrome-extension://"): return r.continue_()
                if url == BASE + "/api/health": return r.fulfill(json={"ok": True,"ext": 2,"profile": "/profile"})
                if url.startswith(BASE + "/api/profile"): return r.fulfill(json=profile)
                if url == BASE + "/api/mark":
                    return r.fulfill(status=503, json={"error": "cannot save"})
                if url == BASE + "/api/ext/open":
                    assert r.request.post_data_json["subs_only"]
                    return r.fulfill(json={"id": VID, "cached": True, "data": video})
                if url == "https://www.youtube.com/watch?v=" + VID:
                    return r.fulfill(content_type="text/html",body='<div id="movie_player" style="height:400px"><video muted></video></div>')
                events.append(url)
                return r.abort()

            ctx.route("**/*",route)
            page = ctx.new_page()
            page.on("pageerror", lambda e: events.append(str(e)))
            page.goto("https://www.youtube.com/watch?v=" + VID)
            words = page.locator(".asubs-w")
            expect(words).to_have_text(["house","fog"])
            popup = ctx.new_page()
            popup.goto(origin + "/popup.html")
            with ctx.expect_page() as opened: popup.locator("#irtLink").click()
            settings = opened.value
            settings.on("pageerror", lambda e: events.append(str(e)))
            expect(settings.locator("#status")).to_contain_text("原有判词")
            def select():
                settings.locator("#table").set_input_files(tab_file)
                settings.locator("#parameters").set_input_files(param_file)
            select()
            settings.locator("#apply").click()
            expect(settings.locator("#status")).to_contain_text("试用已开启")
            expect(words).to_have_text(["orbit","fog"])
            page.locator(".asubs-t[data-n='0']").click()
            expect(page.locator(".asubs-card-why")).to_contain_text("59.9%")
            page.locator(".asubs-card [data-act=close]").click()
            worker.evaluate("() => chrome.storage.local.set({settings:{pref:'less'}})")
            expect(words).to_have_text(["fog"])
            worker.evaluate("() => chrome.storage.local.set({settings:{pref:'normal'}})")
            expect(words).to_have_text(["orbit","fog"])
            settings.locator("#toggle").click()
            expect(words).to_have_text(["house","fog"])
            settings.locator("#toggle").click()
            page.reload()
            expect(words).to_have_text(["orbit","fog"])
            page.locator(".asubs-t[data-n='0']").click()
            page.locator(".asubs-card [data-act=known]").click()
            expect(page.locator(".asubs-pill")).to_contain_text("标记没保存")
            expect(words).to_have_text(["orbit","fog"])
            old_marks = profile.pop("marks")
            page.reload()
            expect(words).to_have_text(["fog"])
            profile["marks"] = old_marks
            page.reload()
            expect(words).to_have_text(["orbit","fog"])
            tab_file.write_bytes(tab_file.read_bytes() + b" ")
            select(); settings.locator("#apply").click()
            expect(settings.locator("#error")).to_contain_text("指纹")
            expect(words).to_have_text(["orbit","fog"])
            tab_file.write_bytes(tab_file.read_bytes()[:-1])
            select(); settings.locator("#field").select_option("b_frequency"); settings.locator("#apply").click()
            expect(words).to_have_text(["house","fog"])
            settings.locator("#remove").click()
            expect(settings.locator("#status")).to_contain_text("原有判词")
            assert worker.evaluate("async () => Object.keys(await chrome.storage.local.get(['irtEnabled','irtPreview'])).length") == 0
            assert not events, events
        finally:
            ctx.close()
    print("Passed measured IRT preview, explicit marks, presets, both scales, fallback and local removal.")


if __name__ == "__main__":
    check()
